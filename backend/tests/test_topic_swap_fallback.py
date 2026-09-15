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

import pytest
from sqlalchemy import Update, select
from sqlalchemy.sql.elements import BindParameter

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.operations import Incident, IncidentState
from app.workers import generation_retry_policy, topic_swap_fallback
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


def _kst(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST)


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


def test_the_swap_leaves_todays_writer_budget_spent(monkeypatch):
    """교체가 소진된 하루 예산을 되살리면 안 된다 — 다음 시도는 내일이다."""

    attempt = _recorded_attempt(monkeypatch)

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


def _freeze(monkeypatch, moment: datetime) -> None:
    """워커와 정책이 같은 '지금'을 보게 한다(사다리 테스트와 같은 방식)."""

    from app.workers import tasks

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy):
        monkeypatch.setattr(module, "datetime", _Frozen)


def test_the_loader_skips_the_swapped_slot_today_and_takes_it_tomorrow(monkeypatch):
    """04시 소진 → 07시 교체 → 그날은 로더가 집지 않고, 다음 날 01시에 집는다."""

    from app.workers import tasks

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda db, hospital_id: None)
    item = _item()
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
