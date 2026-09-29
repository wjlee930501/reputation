"""마지막 폴백 계단 — 본문 슬롯 주제 교체의 판정·초기화·인시던트 경계.

실제 SQL 가드(판·상태·claim 조건부 UPDATE, 후보 술어)는
`tests/integration/test_topic_swap_fallback_postgres.py`가 확인한다. 여기서는 어떤 슬롯이
후보인지, 무엇을 비우는지, 인시던트 epoch가 어떻게 넘어가는지를 고정한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import arrow
import pytest
from sqlalchemy import Update, select
from sqlalchemy.sql.elements import BindParameter

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.essence import PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, IncidentState
from app.workers import generation_retry_policy, nightly_generation_batch, topic_swap_fallback
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_incident_control import generation_incident_dedupe_key
from app.workers.generation_retry_policy import GenerationRetryClass

# autouse fixture가 모듈 함수를 교체하므로 원본을 미리 잡아 둔다.
_RECOVER_INCIDENT = topic_swap_fallback._recover_incident_async
_RECORD_ATTEMPT = topic_swap_fallback._record_topic_swapped_attempt

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 16, 1, 0, tzinfo=UTC)
SLOT = date(2026, 9, 16)


def _attempt(reason="GENERATION_REJECTED", retry_class=GenerationRetryClass.OPERATOR_REQUIRED):
    return {GENERATION_ATTEMPT_KEY: {"reason": reason, "retry_class": retry_class.value}}


def _item(**overrides):
    values = {
        "id": uuid.uuid4(),
        "hospital_id": uuid.uuid4(),
        "schedule_id": uuid.uuid4(),
        "scheduled_date": SLOT,
        "sequence_no": 3,
        "content_type": ContentType.FAQ,
        "carried_over_from": None,
        "status": ContentStatus.DRAFT,
        "content_revision": 4,
        "title": "허리디스크 초기 증상",
        "query_target_id": uuid.uuid4(),
        "exposure_action_id": uuid.uuid4(),
        "content_brief": {"operator_notes": ["원장 확인 문구"], "target_query": "허리디스크 초기 증상"},
        "essence_check_summary": _attempt(),
        "topic_swap_history": None,
        "first_published_at": None,
        "human_edited_at": None,
        "generation_claim_token": None,
        "generation_claimed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _target(name="대장내시경 수면 여부"):
    return SimpleNamespace(id=uuid.uuid4(), name=name)


class _FakeDB:
    """후보 select → swapped select 순서로 답하고 UPDATE는 rowcount만 흉내 낸다."""

    def __init__(self, candidates, *, swapped=(), incident=None, rowcount=1):
        self._content_selects = [list(candidates), list(swapped)]
        self._incident = incident
        self.rowcount = rowcount
        self.updates: list[Update] = []
        self.commits = 0

    def execute(self, statement):
        if isinstance(statement, Update):
            self.updates.append(statement)
            return SimpleNamespace(rowcount=self.rowcount)
        entity = statement.column_descriptions[0]["entity"]
        if entity is Incident:
            return SimpleNamespace(scalar_one_or_none=lambda: self._incident)
        rows = self._content_selects.pop(0) if self._content_selects else []
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    def commit(self):
        self.commits += 1

    def refresh(self, _item):
        # Core UPDATE 뒤 추적 객체를 다시 읽는 동작 — 더블에서는 할 일이 없다.
        return None


def _plain(value):
    """`.values()`에 담긴 바인드 매개변수를 파이썬 값으로 되돌린다."""

    return value.value if isinstance(value, BindParameter) else value


def _update_values(db: _FakeDB, table: str) -> dict:
    for statement in db.updates:
        if statement.table.name == table:
            return {key.name: _plain(value) for key, value in statement._values.items()}
    raise AssertionError(f"{table} UPDATE가 나가지 않았다")


def _values(db: _FakeDB) -> dict:
    return _update_values(db, "content_items")


@pytest.fixture
def chosen_target(monkeypatch):
    target = _target()
    monkeypatch.setattr(
        topic_swap_fallback, "_choose_target", lambda db, **kwargs: target
    )
    return target


@pytest.fixture(autouse=True)
def recorded_attempts(monkeypatch):
    """교체 뒤 시도 기록 쓰기는 `tasks` 경로를 타므로 더블 DB에서는 호출만 잡는다."""

    calls: list[uuid.UUID] = []

    def _record(db, item, *, now):
        calls.append(item.id)

    monkeypatch.setattr(topic_swap_fallback, "_record_topic_swapped_attempt", _record)
    return calls


@pytest.fixture(autouse=True)
def no_incident_recovery(monkeypatch):
    calls: list[uuid.UUID] = []

    async def _recover(incident_id):
        calls.append(incident_id)
        return True

    monkeypatch.setattr(topic_swap_fallback, "_recover_incident_async", _recover)
    return calls


def _run(db):
    return topic_swap_fallback.swap_exhausted_topics(
        db, window_start=SLOT - timedelta(days=7), window_end=SLOT, now=NOW
    )


# ── 후보 판정 ────────────────────────────────────────────────────────────────


def test_exhausted_body_sample_is_a_candidate():
    assert topic_swap_fallback.exhausted_body_sample_reason(_item()) == "GENERATION_REJECTED"


@pytest.mark.parametrize(
    "overrides",
    [
        # 이미지 표본 실패는 "인증 이미지 빌려 쓰기"라는 자기 폴백이 있다.
        {"essence_check_summary": _attempt(reason="IMAGE_GENERATION_RETRIES_EXHAUSTED")},
        # 아직 자동 복구가 소유한 상태.
        {
            "essence_check_summary": _attempt(
                retry_class=GenerationRetryClass.SAMPLE_RECOVERABLE
            )
        },
        # 승인 자료가 틀렸다는 종착 — 주제를 바꿔도 해결되지 않는다.
        {"essence_check_summary": _attempt(retry_class=GenerationRetryClass.INPUT_CHANGE_REQUIRED)},
        # 이미 한 번 바꿨다.
        {"topic_swap_history": [{"to_target_id": "x"}]},
    ],
)
def test_non_candidates_are_never_swapped(overrides):
    assert topic_swap_fallback.exhausted_body_sample_reason(_item(**overrides)) is None


def test_model_declared_hard_finding_is_not_swapped():
    item = _item(
        essence_check_summary={
            **_attempt(reason="CONTENT_AI_HARD_FINDING"),
            "ai_review": {"findings": [{"severity": "HARD"}]},
        }
    )

    assert topic_swap_fallback.exhausted_body_sample_reason(item) is None


def test_synthetic_uncertain_finding_is_still_swapped():
    item = _item(
        essence_check_summary={
            **_attempt(reason="CONTENT_AI_HARD_FINDING"),
            "ai_review": {"findings": [{"severity": "UNCERTAIN"}]},
        }
    )

    assert topic_swap_fallback.exhausted_body_sample_reason(item) == "CONTENT_AI_HARD_FINDING"


def test_published_and_human_edited_rows_are_excluded_by_the_candidate_sql():
    statement = topic_swap_fallback._candidate_stmt(SLOT, SLOT, NOW)
    compiled = str(statement)

    assert "first_published_at IS NULL" in compiled
    assert "human_edited_at IS NULL" in compiled


# ── 교체 ────────────────────────────────────────────────────────────────────


def test_swap_resets_the_slot_and_keeps_the_month_accounting(chosen_target):
    item = _item()
    db = _FakeDB([item])

    report = _run(db)

    assert report.swapped == 1
    values = _values(db)
    assert values["query_target_id"] == chosen_target.id
    assert values["exposure_action_id"] is None
    assert values["title"] is None
    assert values["body"] is None
    assert values["references_list"] is None
    assert values["essence_check_summary"] is None  # 시도 기록·독립 검수 메타를 함께 비운다
    assert values["generated_at"] is None
    assert values["generation_claim_token"] is None
    assert values["brief_status"] is None
    # 운영자 메모는 새 brief로 이월된다.
    assert values["content_brief"]["operator_notes"] == ["원장 확인 문구"]
    # 계약 월·이월 회계를 바꾸는 필드는 UPDATE에 아예 없다.
    for untouched in (
        "scheduled_date",
        "sequence_no",
        "content_type",
        "schedule_id",
        "carried_over_from",
        "first_published_at",
        "first_published_by",
    ):
        assert untouched not in values


def test_swap_clears_every_image_binding(chosen_target):
    db = _FakeDB([_item()])

    _run(db)

    values = _values(db)
    for field in (
        "image_url",
        "image_prompt",
        "image_policy_verified_at",
        "image_content_hash",
        "image_subject_hash",
        "image_policy_version",
        "image_reused_from_content_id",
        "image_fallback_source",
    ):
        assert values[field] is None


def test_swap_unlinks_the_previous_exposure_action(chosen_target):
    item = _item()
    db = _FakeDB([item])

    _run(db)

    assert [statement.table.name for statement in db.updates] == [
        "content_items",
        "exposure_actions",
    ]
    assert _update_values(db, "exposure_actions") == {"linked_content_id": None}


def test_history_entry_records_the_superseded_episode(chosen_target):
    item = _item()
    incident = SimpleNamespace(id=uuid.uuid4(), episode_seq=2)
    db = _FakeDB([item], incident=incident)

    _run(db)

    entry = _values(db)["topic_swap_history"][0]
    assert entry["from_target_id"] == str(item.query_target_id)
    assert entry["from_title"] == "허리디스크 초기 증상"
    assert entry["to_target_id"] == str(chosen_target.id)
    assert entry["reason_code"] == "GENERATION_REJECTED"
    assert entry["swapped_at"] == NOW.isoformat()
    assert (entry["revision_before"], entry["revision_after"]) == (4, 5)
    assert entry["superseded_incident_id"] == str(incident.id)
    assert entry["superseded_episode_seq"] == 2
    assert entry["incident_recovered"] is False


def test_no_candidate_target_leaves_the_slot_alone(monkeypatch):
    monkeypatch.setattr(topic_swap_fallback, "_choose_target", lambda db, **kwargs: None)
    db = _FakeDB([_item()])

    report = _run(db)

    assert (report.considered, report.swapped, report.no_candidate_target) == (1, 0, 1)
    assert db.updates == []


def test_similar_topic_is_not_worth_swapping(monkeypatch):
    monkeypatch.setattr(
        topic_swap_fallback,
        "_choose_target",
        lambda db, **kwargs: _target("허리디스크 초기증상 자가진단"),
    )
    db = _FakeDB([_item()])

    report = _run(db)

    assert (report.swapped, report.similar_topic) == (0, 1)
    assert db.updates == []


def test_write_conflict_abandons_the_swap(chosen_target):
    db = _FakeDB([_item()], rowcount=0)

    report = _run(db)

    assert (report.swapped, report.write_conflicts) == (0, 1)
    # 조건부 UPDATE가 0행이면 백링크도 풀지 않는다.
    assert [s.table.name for s in db.updates] == ["content_items"]


def test_the_conditional_update_guards_revision_status_and_claim(chosen_target):
    db = _FakeDB([_item()])

    _run(db)

    where = str(db.updates[0]).lower()
    assert "content_revision =" in where
    assert "status =" in where
    assert "generation_claim_token is null" in where
    assert "generation_claimed_at <" in where


# ── 인시던트 경계 ────────────────────────────────────────────────────────────


def test_swapped_slot_opens_a_new_incident_key():
    item_id = uuid.uuid4()
    before = generation_incident_dedupe_key(item_id, "GENERATION_REJECTED")
    after = generation_incident_dedupe_key(item_id, "GENERATION_REJECTED", topic_swap_count=1)

    assert before != after
    assert topic_swap_fallback.generation_incident_dedupe_key is generation_incident_dedupe_key


def test_reconcile_closes_only_the_superseded_incident(no_incident_recovery):
    superseded = uuid.uuid4()
    swapped_item = _item(
        topic_swap_history=[
            {"superseded_incident_id": str(superseded), "incident_recovered": False}
        ]
    )
    db = _FakeDB([], swapped=[swapped_item])

    report = _run(db)

    assert no_incident_recovery == [superseded]
    assert report.incidents_recovered == 1
    assert _values(db)["topic_swap_history"] == [
        {"superseded_incident_id": str(superseded), "incident_recovered": True}
    ]


def test_reconcile_is_idempotent_for_already_closed_history(no_incident_recovery):
    swapped_item = _item(
        topic_swap_history=[
            {"superseded_incident_id": str(uuid.uuid4()), "incident_recovered": True}
        ]
    )
    db = _FakeDB([], swapped=[swapped_item])

    report = _run(db)

    assert no_incident_recovery == []
    assert report.incidents_recovered == 0
    assert db.updates == []


def test_a_new_failure_after_the_swap_is_never_recovered_by_the_cleanup(no_incident_recovery):
    """교체 뒤 새 주제가 같은 코드로 실패해 연 건은 history가 가리키지 않는다."""

    superseded = uuid.uuid4()
    new_episode = uuid.uuid4()
    swapped_item = _item(
        topic_swap_history=[
            {"superseded_incident_id": str(superseded), "incident_recovered": False}
        ]
    )
    db = _FakeDB([], swapped=[swapped_item])

    _run(db)

    assert new_episode not in no_incident_recovery


@pytest.mark.asyncio
async def test_recover_incident_closes_open_episodes(monkeypatch):
    incident = _incident(IncidentState.OPEN.value, version=3)
    retrying = _incident(IncidentState.RETRYING.value, version=4, incident_id=incident.id)
    calls: list[tuple[str, str, str]] = []

    async def _mark_retrying(db, incident_id, *, expected_version, actor, reason):
        calls.append(("retrying", actor, reason))
        return retrying

    async def _mark_recovered(db, incident_id, *, expected_version, observed_success, actor, reason):
        calls.append(("recovered", actor, reason))
        assert observed_success is True
        return retrying

    monkeypatch.setattr(topic_swap_fallback, "mark_retrying", _mark_retrying)
    monkeypatch.setattr(topic_swap_fallback, "mark_recovered", _mark_recovered)
    monkeypatch.setattr(
        topic_swap_fallback, "get_async_sessionmaker", lambda: _FakeAsyncSessions(incident)
    )

    assert await _RECOVER_INCIDENT(incident.id) is True
    assert calls == [
        ("retrying", topic_swap_fallback.TOPIC_SWAP_ACTOR, topic_swap_fallback.TOPIC_SWAP_REASON),
        ("recovered", topic_swap_fallback.TOPIC_SWAP_ACTOR, topic_swap_fallback.TOPIC_SWAP_REASON),
    ]


@pytest.mark.asyncio
async def test_recover_incident_leaves_acknowledged_episodes_alone(monkeypatch):
    incident = _incident(IncidentState.ACKNOWLEDGED.value, version=1)

    async def _fail(*args, **kwargs):  # pragma: no cover - 호출되면 실패다
        raise AssertionError("확인 완료된 episode를 건드렸다")

    monkeypatch.setattr(topic_swap_fallback, "mark_retrying", _fail)
    monkeypatch.setattr(topic_swap_fallback, "mark_recovered", _fail)
    monkeypatch.setattr(
        topic_swap_fallback, "get_async_sessionmaker", lambda: _FakeAsyncSessions(incident)
    )

    assert await _RECOVER_INCIDENT(incident.id) is True


def _incident(state: str, *, version: int, incident_id: uuid.UUID | None = None) -> Incident:
    incident = Incident()
    incident.id = incident_id or uuid.uuid4()
    incident.state = state
    incident.version = version
    return incident


class _FakeAsyncSessions:
    def __init__(self, incident):
        self._incident = incident

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, incident_id):
        return self._incident

    async def commit(self):
        return None


# ── 스윕과의 통합 ────────────────────────────────────────────────────────────


def test_recovery_sweep_runs_the_swap_pass_before_the_loader(monkeypatch):
    from app.workers import tasks

    order: list[str] = []

    monkeypatch.setattr(
        tasks,
        "swap_exhausted_topics",
        lambda db, **kwargs: order.append("swap") or topic_swap_fallback.SwapReport(),
    )
    monkeypatch.setattr(
        tasks,
        "_dispatch_generation_batch",
        lambda *args, **kwargs: order.append("loader") or 0,
    )
    monkeypatch.setattr(tasks, "require_dispatch", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _NullSession())
    monkeypatch.setattr(
        tasks, "GenerationBatchRecorder", lambda *args, **kwargs: _NullRecorder()
    )

    tasks.overnight_content_generation_recovery()

    assert order == ["swap", "loader"]


class _NullSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _NullRecorder:
    def finish(self):
        return None


def test_candidate_select_targets_content_items():
    statement = topic_swap_fallback._candidate_stmt(SLOT, SLOT, NOW)

    assert statement.column_descriptions[0]["entity"] is ContentItem
    assert select(ContentItem).column_descriptions[0]["entity"] is ContentItem


# ── 교체 뒤의 하루 예산 ──────────────────────────────────────────────────────


def _kst(year, month, day, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=KST)


def _recorded_attempt(monkeypatch, *, scheduled_date=SLOT, now=NOW) -> dict:
    """`_record_topic_swapped_attempt`가 정본 경로에 넘기는 시도 기록을 잡는다."""

    captured: dict = {}

    def _remember(db, item, philosophy, reason, *, count_attempt, extra):
        captured.update({"reason": reason, "count_attempt": count_attempt, **extra})
        return captured

    monkeypatch.setattr(
        topic_swap_fallback,
        "_tasks",
        lambda: SimpleNamespace(
            _generation_philosophy_sync=lambda db, hospital_id: None,
            _remember_generation_attempt=_remember,
        ),
    )
    _RECORD_ATTEMPT(_FakeDB([]), _item(scheduled_date=scheduled_date), now=now)
    return captured


@pytest.mark.parametrize(
    "scheduled_date",
    [SLOT + timedelta(days=1), SLOT - timedelta(days=1)],
    ids=["before_the_scheduled_day", "after_the_scheduled_day"],
)
def test_the_swap_leaves_todays_writer_budget_spent(monkeypatch, scheduled_date):
    """예정일이 아닌 날의 교체는 소진된 하루 예산을 되살리지 않는다 — 다음 시도는 내일이다."""

    attempt = _recorded_attempt(monkeypatch, scheduled_date=scheduled_date)

    assert attempt["reason"] == topic_swap_fallback.TOPIC_SWAPPED_REASON
    assert attempt["count_attempt"] is False  # 예산을 쓴 결정이 아니다
    assert attempt["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    assert attempt["attempt_period"] == "2026-09-16"  # KST 오늘
    assert attempt["provider_attempt_count"] == generation_retry_policy.SAMPLE_BODY_DAILY_BUDGET
    assert attempt["exhausted_days"] == 0  # 소진 일수는 새 주제에서 다시 센다
    # 내일 01시 복구 스윕이 이 슬롯을 보는 첫 시각이다(오늘 23시 배치의 창에는 없다).
    assert datetime.fromisoformat(attempt["next_retry_at"]) == datetime(
        2026, 9, 17, 1, 0, tzinfo=KST
    )


def test_a_slot_without_a_scheduled_date_gets_no_same_day_grant(monkeypatch):
    """예정일을 읽지 못한 행은 종전 기록 그대로다(당일 1회도, 기한도 없다)."""

    attempt = _recorded_attempt(monkeypatch, scheduled_date=None)

    assert attempt["provider_attempt_count"] == generation_retry_policy.SAMPLE_BODY_DAILY_BUDGET
    assert attempt["next_retry_at"] is None


def _freeze(monkeypatch, moment: datetime) -> None:
    """워커와 정책이 같은 '지금'을 보게 한다(사다리 테스트와 같은 방식)."""

    from app.workers import tasks

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, nightly_generation_batch):
        monkeypatch.setattr(module, "datetime", _Frozen)


def test_the_loader_skips_the_swapped_slot_today_and_takes_it_tomorrow(monkeypatch):
    """D-1 04시 소진 → 07시 교체 → 그날은 로더가 집지 않고, 다음 날(D) 01시에 집는다."""

    from app.workers import tasks

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda db, hospital_id: None)
    item = _item(scheduled_date=SLOT + timedelta(days=1))
    swapped_at = _kst(2026, 9, 16, 7, 0)
    _freeze(monkeypatch, swapped_at)
    _RECORD_ATTEMPT(_FakeDB([]), item, now=swapped_at)

    # 같은 날 남은 스윕(23시)에서도 로더의 술어가 이 슬롯을 거른다.
    _freeze(monkeypatch, _kst(2026, 9, 16, 23, 0))
    assert tasks._generation_attempt_is_unchanged(item, None) is True

    # 다음 날 01시에는 하루 예산이 초기화돼 다시 적격이 된다.
    _freeze(monkeypatch, _kst(2026, 9, 17, 1, 0))
    assert tasks._generation_attempt_is_unchanged(item, None) is False


def test_the_new_topics_first_failure_counts_from_one(monkeypatch):
    """날이 바뀌면 같은 지문이라도 하루 예산은 초기화된다."""

    attempt = _recorded_attempt(monkeypatch)
    count, exhausted_days = generation_retry_policy.sample_budget_spent(
        attempt, "GENERATION_REJECTED", NOW + timedelta(days=1)
    )

    assert (count, exhausted_days) == (1, 0)


# ── 예정일 당일의 교체: 그날 한 번만 새 주제로 쓴다 ─────────────────────────────


_SWAPPED_AT = _kst(2026, 9, 16, 7, 0, 2)  # 07:00 복구 스윕의 교체 pass


class _PageDB:
    """로더의 keyset 페이지를 한 번에 하나씩 돌려준다(복구 사다리 테스트와 같은 더블)."""

    def __init__(self, *pages) -> None:
        self._pages = [list(page) for page in pages]

    def execute(self, _statement):
        page = self._pages.pop(0) if self._pages else []
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: page))

    def commit(self) -> None:
        return None


class _WorkerDB:
    """`_run_generation_item`이 쓰는 세션 표면. 기존 제목 조회는 빈 목록이다."""

    def execute(self, _statement):
        return SimpleNamespace(all=lambda: [])

    def commit(self) -> None:
        return None

    def refresh(self, _item) -> None:
        return None

    def rollback(self) -> None:
        return None

    def expire_all(self) -> None:
        return None


class _Recorder:
    def __init__(self) -> None:
        self.run = SimpleNamespace(id=uuid.uuid4())
        self.states: list = []

    def record(self, _item_id, state, **_kwargs) -> None:
        self.states.append(state)

    def item_run(self, *_args, **_kwargs):
        return SimpleNamespace(id=uuid.uuid4())


class _AutoPublishDB:
    """08:00 `_auto_publish_one`의 조회를 대상 엔티티로 답한다."""

    def __init__(self, item, hospital) -> None:
        self._rows = {ContentItem: item, Hospital: hospital}
        self.added: list = []

    def execute(self, statement):
        row = self._rows[statement.column_descriptions[0]["entity"]]
        return SimpleNamespace(scalar_one_or_none=lambda: row)

    def add(self, value) -> None:
        self.added.append(value)

    def commit(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _swapped_slot(philosophy) -> SimpleNamespace:
    """교체 UPDATE 직후의 슬롯 — 본문·이미지·시도 기록이 비고 history가 한 칸이다."""

    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="당일교체의원",
        slug="same-day-swap",
        aeo_domain="same-day-swap.example.com",
        treatments=[],
        status=HospitalStatus.ACTIVE,
        site_live=True,
    )
    return _item(
        hospital=hospital,
        hospital_id=hospital.id,
        title=None,
        body=None,
        meta_description=None,
        faq_question=None,
        faq_answer_summary=None,
        references_list=None,
        image_url=None,
        image_prompt=None,
        image_content_hash=None,
        image_subject_hash=None,
        image_policy_version=None,
        image_policy_verified_at=None,
        image_reused_from_content_id=None,
        image_fallback_source=None,
        essence_check_summary=None,
        essence_status=None,
        content_philosophy_id=philosophy.id,
        content_revision=5,
        total_count=12,
        published_at=None,
        published_by=None,
        post_publish_notified_at=None,
        post_publish_reviewed_at=None,
        post_publish_reviewed_by=None,
        topic_swap_history=[{"to_target_id": "new", "incident_recovered": True}],
    )


def _approved_philosophy():
    return SimpleNamespace(
        id=uuid.uuid4(), version=3, status=PhilosophyStatus.APPROVED, avoid_messages=[]
    )


def _patch_generation(monkeypatch, philosophy, slot, *, fail: bool) -> list[uuid.UUID]:
    """공급자 대신 작가 호출 수만 세는 가짜 생성 경로를 건다."""

    from app.workers import tasks

    writer_calls: list[uuid.UUID] = []

    async def allowed(*_args, **_kwargs):
        return SimpleNamespace(allowed=True)

    async def ignore(*_args, **_kwargs):
        return None

    async def fake_writer(*, hospital, item, existing_titles, philosophy, approved_brief):
        writer_calls.append(item.id)
        if fail:
            raise ValueError("GEO hard-fail: references is empty for FAQ")
        title = "대장내시경 전 준비할 점"
        return (
            {
                "title": title,
                "body": "검사 전날의 식사와 복용약을 의료진과 미리 확인합니다.",
                "meta_description": "대장내시경 전 준비할 점을 정리했습니다.",
                "references": [
                    {
                        "title": "질병관리청 국가건강정보포털",
                        "url": "https://health.kdca.go.kr/healthinfo/example",
                    }
                ],
                "faq_question": "대장내시경 전에 무엇을 준비하나요?",
                "faq_answer_summary": "식사 조절과 복용약 확인이 필요합니다.",
            },
            SimpleNamespace(status=None, summary={}),
        )

    def write_back(_db, *, item_id, values, expected_revision, expected_claim_token):
        assert expected_claim_token == slot.generation_claim_token
        for field, value in values.items():
            setattr(slot, field, value)
        return 1

    def certified_image(_db, item, _hospital, _philosophy):
        image_hash = "c" * 64
        item.image_url = f"gs://reputation-images/content/{image_hash}-content.png"
        item.image_content_hash = image_hash
        item.image_subject_hash = tasks.image_subject_hash(item.content_type, item.title)
        item.image_policy_version = tasks.IMAGE_POLICY_VERSION
        item.image_policy_verified_at = datetime.now()
        return tasks.GenerationItemState.SUCCEEDED

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allowed)
    monkeypatch.setattr(
        tasks, "prepare_automatic_content_brief_sync", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(tasks, "_generate_with_auto_review", fake_writer)
    monkeypatch.setattr(tasks, "_generation_summary", lambda *_args: {})
    monkeypatch.setattr(tasks, "write_back_generated_content", write_back)
    monkeypatch.setattr(tasks, "_recover_missing_content_image", certified_image)
    monkeypatch.setattr(tasks, "recover_generation_incidents", ignore)
    monkeypatch.setattr(tasks, "open_generation_incident", ignore)
    return writer_calls


def _claims_at(monkeypatch, item, moment: datetime) -> bool:
    """그 시각의 복구 스윕 로더가 이 슬롯을 claim하는가(실제 로더·실제 적격 술어)."""

    from app.workers import tasks

    _freeze(monkeypatch, moment)
    item.generation_claim_token = None
    item.generation_claimed_at = None
    page_db = _PageDB([item])
    claimed, _truncated, _complete = tasks._load_nightly_generation_batch(
        page_db,
        SLOT - timedelta(days=7),
        SLOT + timedelta(days=2),
        is_eligible=tasks._generation_retry_is_eligible(page_db),
    )
    return [row.id for row in claimed] == [item.id]


def _generate_once(monkeypatch, item, moment: datetime):
    from app.workers import tasks

    _freeze(monkeypatch, moment)
    return tasks._run_generation_item(_WorkerDB(), _Recorder(), item, item.hospital)


def test_a_same_day_swap_is_generated_by_the_same_sweep_and_published_at_eight(monkeypatch):
    """07:00 교체 → 같은 스윕 로더가 새 주제를 한 번 쓴다 → 08:00 자동 발행이 공개한다.

    신기한속내과 f0217d98: 예정일 07:00에 교체된 슬롯이 오늘 예산을 소진으로 기록해 같은
    스윕이 "No content to generate"로 끝나고 08:00 발행을 놓쳤다.
    """

    from app.workers import tasks

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    writer_calls = _patch_generation(monkeypatch, philosophy, item, fail=False)

    _freeze(monkeypatch, _SWAPPED_AT)
    _RECORD_ATTEMPT(_FakeDB([]), item, now=_SWAPPED_AT)
    attempt = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    # 오늘 예산은 한 회만 남고, 기한은 지금이다(다음 스윕이 아니라 이 스윕).
    assert attempt["provider_attempt_count"] == generation_retry_policy.SAMPLE_BODY_DAILY_BUDGET - 1
    assert datetime.fromisoformat(attempt["next_retry_at"]) == _SWAPPED_AT

    # 교체 pass 바로 뒤의 같은 07:00 스윕 로더가 집는다.
    assert _claims_at(monkeypatch, item, _kst(2026, 9, 16, 7, 0, 3)) is True
    state, code, _message = _generate_once(monkeypatch, item, _kst(2026, 9, 16, 7, 0, 4))

    assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
    assert writer_calls == [item.id]

    # 08:00 자동 발행 — 실제 assess_content_publication을 지난다.
    publish_db = _AutoPublishDB(item, item.hospital)
    _freeze(monkeypatch, _kst(2026, 9, 16, 8, 0))
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_a, **_kw: arrow.get(_kst(2026, 9, 16, 8, 0))
    )
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: publish_db)
    monkeypatch.setattr(
        tasks, "get_current_approved_philosophy_sync", lambda *_args: philosophy
    )
    monkeypatch.setattr(tasks, "ensure_site_revalidate_configured", lambda: None)

    payload = tasks._auto_publish_one(item.id)

    assert payload is not None and payload["kind"] == "published"
    assert item.status is ContentStatus.PUBLISHED
    assert writer_calls == [item.id]  # 발행 경로는 작가를 다시 부르지 않는다


def test_the_same_day_grant_is_one_writer_session_only(monkeypatch):
    """그 1회가 실패하면 그날 남은 스윕은 다시 사지 않고, 내일 01시에야 다시 집는다."""

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    writer_calls = _patch_generation(monkeypatch, philosophy, item, fail=True)

    _freeze(monkeypatch, _SWAPPED_AT)
    _RECORD_ATTEMPT(_FakeDB([]), item, now=_SWAPPED_AT)
    assert _claims_at(monkeypatch, item, _kst(2026, 9, 16, 7, 0, 3)) is True
    _generate_once(monkeypatch, item, _kst(2026, 9, 16, 7, 0, 4))
    assert writer_calls == [item.id]

    attempt = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert attempt["reason"] == "GENERATION_REJECTED"
    assert attempt["provider_attempt_count"] == generation_retry_policy.SAMPLE_BODY_DAILY_BUDGET
    for moment in (
        _kst(2026, 9, 16, 7, 0, 30),
        _kst(2026, 9, 16, 12, 0),
        _kst(2026, 9, 16, 18, 0),
        _kst(2026, 9, 16, 22, 0),
        _kst(2026, 9, 16, 23, 0),
    ):
        assert _claims_at(monkeypatch, item, moment) is False, moment
    # 워커 쪽 SKIPPED 판정도 같은 말을 한다 — 로더를 우회해도 작가를 다시 사지 않는다.
    _generate_once(monkeypatch, item, _kst(2026, 9, 16, 12, 0))
    assert writer_calls == [item.id]

    # 날이 바뀌면 정상 예산 규칙대로 다시 적격이다.
    assert _claims_at(monkeypatch, item, _kst(2026, 9, 17, 1, 0)) is True


def test_a_second_same_day_swap_pass_grants_nothing(chosen_target, recorded_attempts):
    """교체는 슬롯 평생 한 번이다 — 같은 날 다시 소진돼 보여도 두 번째 당일 1회는 없다."""

    item = _item(
        topic_swap_history=[{"to_target_id": "new", "incident_recovered": True}],
        essence_check_summary=_attempt(),  # 새 주제도 소진된 것처럼 보이는 기록
    )
    db = _FakeDB([item])

    report = _run(db)  # NOW는 SLOT(예정일) 당일 KST다

    assert (report.considered, report.swapped) == (0, 0)
    assert recorded_attempts == []
    assert db.updates == []


# ── 07:45·08:00 게이트는 교체 기록을 덮지 않는다 ─────────────────────────────────


class _GateDB:
    """07:45 `_page_morning_stored_publication_gates`의 후보 조회를 이 슬롯으로 답한다."""

    def __init__(self, item) -> None:
        self._item = item
        self.added: list = []

    def execute(self, _statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [self._item]))

    def add(self, value) -> None:
        self.added.append(value)

    def commit(self) -> None:
        return None


def _run_morning_gates(monkeypatch, item, philosophy, gate_day: date) -> tuple[list, list]:
    """07:45 게이트와 08:00 발행기를 실제 판정·기록 경로로 돌리고 보고된 코드를 잡는다."""

    from app.workers import tasks

    incidents: list[str] = []
    digests: list[str] = []

    async def capture_incident(**kwargs):
        incidents.append(kwargs["code"])

    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(
        tasks,
        "ensure_publication_block_run",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4()),
    )
    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)
    monkeypatch.setattr(
        tasks,
        "enqueue_generation_blocked_digest_sync",
        lambda _db, _day, _batch, outcomes: digests.extend(row["code"] for row in outcomes),
    )

    gate_at = _kst(gate_day.year, gate_day.month, gate_day.day, 7, 45)
    _freeze(monkeypatch, gate_at)
    assert tasks._page_morning_stored_publication_gates(
        _GateDB(item), now_kst=arrow.get(gate_at)
    ) == 1

    publish_at = _kst(gate_day.year, gate_day.month, gate_day.day, 8, 0)
    _freeze(monkeypatch, publish_at)
    monkeypatch.setattr(tasks.arrow, "now", lambda *_a, **_kw: arrow.get(publish_at))
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _AutoPublishDB(item, item.hospital))
    outcome = tasks._auto_publish_one(item.id)
    assert outcome is not None and outcome["kind"] == "blocked"
    return incidents + [outcome["code"]], digests


@pytest.mark.parametrize(
    ("swapped_at", "next_sweep"),
    [
        # 예정일 다음 날 07:00의 교체(스윕 창은 오늘-7부터다) — 오늘 예산은 소진, 내일 01시.
        (_kst(2026, 9, 17, 7, 0, 2), _kst(2026, 9, 18, 1, 0)),
        # 예정일 당일 교체인데 같은 스윕 로더가 닿지 못했다(상한·잘림) — 당일 1회가 남아 있다.
        (_SWAPPED_AT, _kst(2026, 9, 17, 1, 0)),
    ],
    ids=["past_due_swap", "same_day_swap_not_reached"],
)
def test_the_morning_gates_keep_the_swap_record_so_a_sweep_writes_the_new_topic(
    monkeypatch, swapped_at, next_sweep
):
    """교체 → 07:45 → 08:00 → 다음 적격 스윕이 새 주제를 쓴다.

    게이트가 빈 슬롯의 증상(CONTENT_NOT_GENERATED)으로 교체 기록을 덮으면 분류가
    OPERATOR_REQUIRED·기한 없음으로 굳어 로더가 영영 집지 않고, 교체 이력이 있어
    다시 교체되지도 않았다. 보고 코드(인시던트·요약)는 종전 그대로 CONTENT_NOT_GENERATED다.
    """

    from app.workers import tasks

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    writer_calls = _patch_generation(monkeypatch, philosophy, item, fail=False)

    _freeze(monkeypatch, swapped_at)
    _RECORD_ATTEMPT(_FakeDB([]), item, now=swapped_at)
    swapped = dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])
    assert swapped["reason"] == topic_swap_fallback.TOPIC_SWAPPED_REASON
    assert swapped["next_retry_at"] is not None

    reported, digested = _run_morning_gates(monkeypatch, item, philosophy, swapped_at.date())

    # 기록은 한 글자도 바뀌지 않는다 — 원인·분류·기한·계수 모두.
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == swapped
    assert swapped["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    # 보고 경로는 종전 그대로다: 07:45 인시던트·08:00 차단, 두 요약 모두 같은 코드.
    assert reported == ["CONTENT_NOT_GENERATED", "CONTENT_NOT_GENERATED"]
    assert digested == ["CONTENT_NOT_GENERATED"]
    assert writer_calls == []  # 게이트는 작가를 부르지 않는다

    # 로더와 워커가 같은 기록을 읽고 다음 적격 스윕에서 새 주제를 한 번 쓴다.
    _freeze(monkeypatch, next_sweep)
    assert tasks.retry_is_due(item.essence_check_summary[GENERATION_ATTEMPT_KEY]) is True
    assert tasks._generation_attempt_is_unchanged(item, philosophy) is False
    assert _claims_at(monkeypatch, item, next_sweep) is True
    state, code, _message = _generate_once(monkeypatch, item, next_sweep + timedelta(seconds=1))

    assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
    assert writer_calls == [item.id]
    assert item.title == "대장내시경 전 준비할 점"


@pytest.mark.parametrize(
    ("gate_code", "kept"),
    [
        ("CONTENT_NOT_GENERATED", True),
        # 빈 슬롯의 증상이 아닌 실제 원인은 종전처럼 정본 기록이 된다.
        ("CONTENT_AUTHORITY_CHANGED", False),
    ],
)
def test_only_the_empty_slot_symptom_leaves_the_swap_record_alone(
    monkeypatch, gate_code, kept
):
    from app.workers import tasks

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    _freeze(monkeypatch, _SWAPPED_AT)
    _RECORD_ATTEMPT(_FakeDB([]), item, now=_SWAPPED_AT)
    swapped = dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])

    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, gate_code)

    after = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert (after == swapped) is kept
    assert after["reason"] == (topic_swap_fallback.TOPIC_SWAPPED_REASON if kept else gate_code)
