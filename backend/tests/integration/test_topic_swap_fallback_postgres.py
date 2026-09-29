"""주제 교체 폴백의 실제 SQL — 후보 술어와 조건부 UPDATE의 가드.

mock으로는 확인할 수 없는 것만 본다: 어떤 행이 `FOR UPDATE SKIP LOCKED` 후보로 잡히는가
(공개 이력·사람 편집·이미 교체한 글·활성 claim), 그리고 판·상태·claim이 바뀐 사이에 쓰면
정말로 0행이 되는가. 인시던트 종결은 자기 async 세션을 쓰므로 여기서는 대상 선택만 본다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import arrow
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus
from app.models.operations import Incident, IncidentSeverity
from app.workers import (
    generation_retry_policy,
    nightly_generation_batch,
    tasks,
    topic_swap_fallback,
)
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_incident_control import generation_incident_dedupe_key
from app.workers.generation_retry_policy import (
    SAMPLE_BODY_DAILY_BUDGET,
    GenerationRetryClass,
)

NOW = datetime(2026, 9, 16, 1, 0, tzinfo=UTC)
SLOT = date(2026, 9, 16)
EXHAUSTED = {
    GENERATION_ATTEMPT_KEY: {
        "reason": "GENERATION_REJECTED",
        "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
    }
}


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def captured_recoveries(monkeypatch):
    """인시던트 종결은 별도 async 세션이라 테스트 트랜잭션 밖이다 — 호출만 잡는다."""

    calls: list[uuid.UUID] = []

    async def _recover(incident_id):
        calls.append(incident_id)
        return True

    monkeypatch.setattr(topic_swap_fallback, "_recover_incident_async", _recover)
    return calls


def _seed_hospital(conn) -> uuid.UUID:
    hospital_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status, site_live) "
            "VALUES (:id, '주제교체의원', :slug, 'ACTIVE', true)"
        ),
        {"id": hospital_id, "slug": f"swap-{uuid.uuid4().hex[:8]}"},
    )
    for name, condition in (("허리디스크 초기 증상", "허리디스크"), ("대장내시경 수면 여부", "대장용종")):
        conn.execute(
            text(
                "INSERT INTO ai_query_targets (id, hospital_id, name, target_intent, "
                "region_terms, decision_criteria, platforms, competitor_names, "
                "condition_or_symptom, patient_language, priority, status) VALUES "
                "(:id, :hospital_id, :name, 'INFORMATION', '[]', '[]', '[]', '[]', "
                ":condition, 'ko', 'NORMAL', 'ACTIVE')"
            ),
            {
                "id": uuid.uuid4(),
                "hospital_id": hospital_id,
                "name": name,
                "condition": condition,
            },
        )
    return hospital_id


def _seed_item(conn, hospital_id: uuid.UUID, **overrides) -> uuid.UUID:
    schedule_id = uuid.uuid4()
    item_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO content_schedules (id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hospital_id, 'PLAN_12', '[1]'::json, DATE '2026-09-01')"
        ),
        {"id": schedule_id, "hospital_id": hospital_id},
    )
    # 실패한 주제와 제목을 일부러 같은 계열로 둔다 — 교체 후보는 다른 계열이어야 한다.
    target_id = conn.execute(
        text("SELECT id FROM ai_query_targets WHERE hospital_id=:h AND name=:name"),
        {"h": hospital_id, "name": "대장내시경 수면 여부"},
    ).scalar_one()
    columns = {
        "id": item_id,
        "sequence_no": 1,
        "hospital_id": hospital_id,
        "schedule_id": schedule_id,
        "query_target_id": target_id,
        "title": "대장내시경 수면 여부 안내",
        "scheduled_date": SLOT,
        "status": ContentStatus.DRAFT.value,
        "summary": '{"generation_attempt": {"reason": "GENERATION_REJECTED",'
        ' "retry_class": "OPERATOR_REQUIRED"}}',
        "first_published_at": None,
        "human_edited_at": None,
        "topic_swap_history": None,
        "claimed_at": None,
        "claim_token": None,
        "image_url": "https://cdn.example/old.png",
    }
    columns.update(overrides)
    conn.execute(
        text(
            "INSERT INTO content_items (id, hospital_id, schedule_id, query_target_id, "
            "content_type, sequence_no, total_count, title, image_url, scheduled_date, status, "
            "essence_check_summary, first_published_at, human_edited_at, topic_swap_history, "
            "generation_claimed_at, generation_claim_token, content_revision) VALUES "
            "(:id, :hospital_id, :schedule_id, :query_target_id, 'FAQ', :sequence_no, 12, :title, "
            ":image_url, :scheduled_date, :status, CAST(:summary AS jsonb), :first_published_at, "
            ":human_edited_at, CAST(:topic_swap_history AS jsonb), :claimed_at, :claim_token, 4)"
        ),
        columns,
    )
    return item_id


def _swap(session) -> topic_swap_fallback.SwapReport:
    return topic_swap_fallback.swap_exhausted_topics(
        session, window_start=SLOT - timedelta(days=7), window_end=SLOT, now=NOW
    )


def test_unclaimed_exhausted_slot_is_swapped_and_fully_reset(pg_conn, pg_session):
    hospital_id = _seed_hospital(pg_conn)
    # 예정일이 아닌 날(catch-up 창 안의 지난 슬롯)의 교체다. 당일 교체는 아래 테스트가 본다.
    past_slot = SLOT - timedelta(days=1)
    item_id = _seed_item(pg_conn, hospital_id, scheduled_date=past_slot)

    report = _swap(pg_session)

    assert report.swapped == 1
    row = pg_session.get(ContentItem, item_id)
    pg_session.refresh(row)
    assert row.title is None
    assert row.image_url is None
    # 옛 시도 기록·독립 검수 메타는 사라지고, 그 자리에 "오늘 예산은 이미 썼다"는
    # 결정 하나만 남는다 — 교체가 같은 날 작가 세션을 되살리지 않게 하는 기록이다.
    attempt = row.essence_check_summary["generation_attempt"]
    assert attempt["reason"] == topic_swap_fallback.TOPIC_SWAPPED_REASON
    assert attempt["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    assert attempt["provider_attempt_count"] == SAMPLE_BODY_DAILY_BUDGET
    assert generation_retry_policy.retry_is_due(attempt, NOW) is False
    assert row.content_revision == 5
    assert row.scheduled_date == past_slot  # 계약 월 회계는 그대로다
    assert len(row.topic_swap_history) == 1
    assert row.topic_swap_history[0]["reason_code"] == "GENERATION_REJECTED"
    assert row.topic_swap_history[0]["to_target_id"] != str(row.topic_swap_history[0]["from_target_id"])


def test_a_same_day_swap_leaves_exactly_one_writer_session_due_now(pg_conn, pg_session):
    """예정일 당일 07:00 교체는 그날 작가 세션 1회를 남기고 지금 바로 집히게 한다."""

    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(pg_conn, hospital_id)  # scheduled_date = SLOT
    swapped_at = datetime(2026, 9, 15, 22, 0, 2, tzinfo=UTC)  # SLOT 07:00:02 KST

    report = topic_swap_fallback.swap_exhausted_topics(
        pg_session,
        window_start=SLOT - timedelta(days=7),
        window_end=SLOT + timedelta(days=2),
        now=swapped_at,
    )

    assert report.swapped == 1
    row = pg_session.get(ContentItem, item_id)
    pg_session.refresh(row)
    attempt = row.essence_check_summary["generation_attempt"]
    assert attempt["reason"] == topic_swap_fallback.TOPIC_SWAPPED_REASON
    assert attempt["attempt_period"] == SLOT.isoformat()
    assert attempt["provider_attempt_count"] == SAMPLE_BODY_DAILY_BUDGET - 1
    assert datetime.fromisoformat(attempt["next_retry_at"]) == swapped_at
    assert generation_retry_policy.retry_is_due(attempt, swapped_at + timedelta(seconds=1))
    # 그 1회를 쓰면 오늘 예산은 소진이다 — 정상 예산 규칙이 경계를 소유한다.
    count, _exhausted_days = generation_retry_policy.sample_budget_spent(
        attempt, "GENERATION_REJECTED", swapped_at + timedelta(minutes=5)
    )
    assert count == SAMPLE_BODY_DAILY_BUDGET


def _freeze(monkeypatch, moment: datetime) -> None:
    """로더·재시도 정책·게이트 기록이 같은 '지금'을 보게 한다(단위 테스트와 같은 방식)."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, nightly_generation_batch):
        monkeypatch.setattr(module, "datetime", _Frozen)


def test_the_morning_gate_keeps_the_swap_record_and_the_next_sweep_claims_it(
    pg_conn, pg_session, monkeypatch
):
    """교체(실제 SQL) → 07:45 게이트(실제 후보 조회·판정) → 다음 적격 스윕 로더가 claim한다.

    게이트가 빈 슬롯의 증상으로 교체 기록을 OPERATOR_REQUIRED로 덮으면 로더의 적격
    술어가 이 슬롯을 영영 거르고, 교체 이력 때문에 다시 교체되지도 않는다.
    """

    kst = ZoneInfo("Asia/Seoul")
    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(pg_conn, hospital_id)  # scheduled_date = SLOT(09-16)
    swapped_at = datetime(2026, 9, 17, 7, 0, 2, tzinfo=kst)  # 예정일 다음 날 07:00 스윕

    report = topic_swap_fallback.swap_exhausted_topics(
        pg_session,
        window_start=swapped_at.date() - timedelta(days=7),
        window_end=swapped_at.date() + timedelta(days=2),
        now=swapped_at.astimezone(UTC),
    )
    assert report.swapped == 1
    row = pg_session.get(ContentItem, item_id)
    pg_session.refresh(row)
    swapped = dict(row.essence_check_summary[GENERATION_ATTEMPT_KEY])
    assert swapped["reason"] == topic_swap_fallback.TOPIC_SWAPPED_REASON
    assert datetime.fromisoformat(swapped["next_retry_at"]) == datetime(2026, 9, 18, 1, 0, tzinfo=kst)

    incidents: list[tuple[uuid.UUID, str]] = []
    digested: list[tuple[uuid.UUID, str]] = []

    async def capture_incident(**kwargs):
        # 인시던트는 자기 async 세션을 쓴다(테스트 트랜잭션 밖) — 보고된 코드만 잡는다.
        incidents.append((kwargs["item_id"], kwargs["code"]))

    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)
    monkeypatch.setattr(
        tasks,
        "ensure_publication_block_run",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4()),
    )
    monkeypatch.setattr(
        tasks,
        "enqueue_generation_blocked_digest_sync",
        lambda _db, _day, _batch, outcomes: digested.extend(
            (row["content_id"], row["code"]) for row in outcomes
        ),
    )
    gate_at = datetime(2026, 9, 17, 7, 45, tzinfo=kst)
    _freeze(monkeypatch, gate_at)
    tasks._page_morning_stored_publication_gates(pg_session, now_kst=arrow.get(gate_at))

    pg_session.refresh(row)
    assert row.essence_check_summary[GENERATION_ATTEMPT_KEY] == swapped
    # 보고 경로는 종전 그대로다.
    assert (item_id, "CONTENT_NOT_GENERATED") in incidents
    assert (item_id, "CONTENT_NOT_GENERATED") in digested

    next_sweep = datetime(2026, 9, 18, 1, 0, tzinfo=kst)
    _freeze(monkeypatch, next_sweep)
    claimed, _truncated, _complete = tasks._load_nightly_generation_batch(
        pg_session,
        next_sweep.date() - timedelta(days=7),
        next_sweep.date() + timedelta(days=2),
        is_eligible=tasks._generation_retry_is_eligible(pg_session),
    )

    assert item_id in {item.id for item in claimed}


class _SessionProxy:
    """`with SyncSessionLocal() as db:`를 테스트 세션에 그대로 붙인다."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


def _capture_gate_reports(monkeypatch):
    """07:45 게이트의 인시던트·요약 보고를 잡는다(위 테스트와 같은 방식)."""

    incidents: list[tuple[uuid.UUID, str]] = []
    digested: list[tuple[uuid.UUID, str]] = []

    async def capture_incident(**kwargs):
        incidents.append((kwargs["item_id"], kwargs["code"]))

    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)
    monkeypatch.setattr(
        tasks,
        "ensure_publication_block_run",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4()),
    )
    monkeypatch.setattr(
        tasks,
        "enqueue_generation_blocked_digest_sync",
        lambda _db, _day, _batch, outcomes: digested.extend(
            (row["content_id"], row["code"]) for row in outcomes
        ),
    )
    return incidents, digested


def test_a_same_day_swap_survives_the_morning_gate_and_is_claimed_the_same_morning(
    pg_conn, pg_session, monkeypatch
):
    """예정일 당일 07:00:02 교체 → 07:45 게이트는 기록을 지키고 보고만 한다 → 로더가 바로 claim한다."""

    kst = ZoneInfo("Asia/Seoul")
    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(pg_conn, hospital_id)  # scheduled_date = SLOT(09-16)
    swapped_at = datetime(2026, 9, 16, 7, 0, 2, tzinfo=kst)

    report = topic_swap_fallback.swap_exhausted_topics(
        pg_session,
        window_start=SLOT - timedelta(days=7),
        window_end=SLOT + timedelta(days=2),
        now=swapped_at.astimezone(UTC),
    )
    assert report.swapped == 1
    row = pg_session.get(ContentItem, item_id)
    pg_session.refresh(row)
    swapped = dict(row.essence_check_summary[GENERATION_ATTEMPT_KEY])
    assert swapped["reason"] == topic_swap_fallback.TOPIC_SWAPPED_REASON
    assert datetime.fromisoformat(swapped["next_retry_at"]) == swapped_at

    incidents, digested = _capture_gate_reports(monkeypatch)
    gate_at = datetime(2026, 9, 16, 7, 45, tzinfo=kst)
    _freeze(monkeypatch, gate_at)
    tasks._page_morning_stored_publication_gates(pg_session, now_kst=arrow.get(gate_at))

    pg_session.refresh(row)
    assert row.essence_check_summary[GENERATION_ATTEMPT_KEY] == swapped
    assert incidents == [(item_id, "CONTENT_NOT_GENERATED")]
    assert digested == [(item_id, "CONTENT_NOT_GENERATED")]

    # 같은 아침의 다음 적격 로더(운영자 재시도·07시대 스윕)가 기다리지 않고 집는다.
    loader_at = gate_at + timedelta(minutes=1)
    _freeze(monkeypatch, loader_at)
    claimed, _truncated, _complete = tasks._load_nightly_generation_batch(
        pg_session,
        loader_at.date() - timedelta(days=7),
        loader_at.date() + timedelta(days=2),
        is_eligible=tasks._generation_retry_is_eligible(pg_session),
    )

    assert [item.id for item in claimed] == [item_id]


def test_the_morning_gate_skips_only_a_live_claim(pg_conn, pg_session, monkeypatch):
    """살아 있는 claim의 행은 손대지 않고, 만료된 claim의 같은 행은 종전처럼 처리한다."""

    kst = ZoneInfo("Asia/Seoul")
    gate_at = datetime(2026, 9, 16, 7, 45, tzinfo=kst)
    hospital_id = _seed_hospital(pg_conn)
    # 시도 기록이 없는 같은 모양의 두 행 — 처리됐다면 게이트가 기록을 새로 남긴다.
    live = _seed_item(
        pg_conn,
        hospital_id,
        sequence_no=1,
        summary="{}",
        claimed_at=gate_at - timedelta(minutes=35),
        claim_token=uuid.uuid4(),
    )
    expired = _seed_item(
        pg_conn,
        hospital_id,
        sequence_no=2,
        summary="{}",
        claimed_at=gate_at - timedelta(hours=2, minutes=1),
        claim_token=uuid.uuid4(),
    )
    before = pg_conn.execute(
        text(
            "SELECT essence_check_summary, content_revision, generation_claimed_at, "
            "generation_claim_token FROM content_items WHERE id=:id"
        ),
        {"id": live},
    ).one()

    incidents, digested = _capture_gate_reports(monkeypatch)
    _freeze(monkeypatch, gate_at)
    tasks._page_morning_stored_publication_gates(pg_session, now_kst=arrow.get(gate_at))
    pg_session.commit()

    after = pg_conn.execute(
        text(
            "SELECT essence_check_summary, content_revision, generation_claimed_at, "
            "generation_claim_token FROM content_items WHERE id=:id"
        ),
        {"id": live},
    ).one()
    assert tuple(after) == tuple(before)
    assert incidents == [(expired, "CONTENT_NOT_GENERATED")]
    assert digested == [(expired, "CONTENT_NOT_GENERATED")]
    processed = pg_session.get(ContentItem, expired)
    pg_session.refresh(processed)
    assert processed.essence_check_summary[GENERATION_ATTEMPT_KEY]["reason"] == (
        "CONTENT_NOT_GENERATED"
    )


def test_the_eight_oclock_publisher_keeps_the_swap_record_and_reports_the_block(
    pg_conn, pg_session, monkeypatch
):
    """08:00 발행기(`_auto_publish_one`)도 빈 교체 슬롯의 증상으로 교체 기록을 덮지 않는다."""

    kst = ZoneInfo("Asia/Seoul")
    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(pg_conn, hospital_id)
    swapped_at = datetime(2026, 9, 16, 7, 0, 2, tzinfo=kst)
    assert (
        topic_swap_fallback.swap_exhausted_topics(
            pg_session,
            window_start=SLOT - timedelta(days=7),
            window_end=SLOT + timedelta(days=2),
            now=swapped_at.astimezone(UTC),
        ).swapped
        == 1
    )
    row = pg_session.get(ContentItem, item_id)
    pg_session.refresh(row)
    swapped = dict(row.essence_check_summary[GENERATION_ATTEMPT_KEY])

    publish_at = datetime(2026, 9, 16, 8, 0, tzinfo=kst)
    monkeypatch.setattr(tasks, "SyncSessionLocal", _SessionProxy(pg_session))
    monkeypatch.setattr(
        tasks,
        "ensure_publication_block_run",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4()),
    )
    monkeypatch.setattr(tasks.arrow, "now", lambda *_args, **_kwargs: arrow.get(publish_at))
    _freeze(monkeypatch, publish_at)

    outcome = tasks._auto_publish_one(item_id)

    assert outcome is not None
    assert (outcome["kind"], outcome["code"]) == ("blocked", "CONTENT_NOT_GENERATED")
    pg_session.refresh(row)
    assert row.status == ContentStatus.DRAFT
    assert row.essence_check_summary[GENERATION_ATTEMPT_KEY] == swapped


def test_expired_claim_is_swapped_but_an_active_one_is_left_alone(pg_conn, pg_session):
    hospital_id = _seed_hospital(pg_conn)
    expired = _seed_item(
        pg_conn,
        hospital_id,
        claimed_at=NOW - timedelta(hours=3),
        claim_token=uuid.uuid4(),
    )
    active = _seed_item(
        pg_conn,
        hospital_id,
        claimed_at=NOW - timedelta(minutes=5),
        claim_token=uuid.uuid4(),
    )

    report = _swap(pg_session)

    assert report.swapped == 1
    assert pg_session.get(ContentItem, expired).topic_swap_history is not None
    assert pg_session.get(ContentItem, active).topic_swap_history is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"first_published_at": datetime(2026, 9, 10, tzinfo=UTC)},
        {"human_edited_at": datetime(2026, 9, 10, tzinfo=UTC)},
        {"topic_swap_history": '[{"reason_code": "GENERATION_REJECTED"}]'},
        {"status": ContentStatus.PUBLISHED.value},
        {"summary": '{"generation_attempt": {"reason": "GENERATION_REJECTED",'
         ' "retry_class": "SAMPLE_RECOVERABLE"}}'},
    ],
)
def test_excluded_rows_are_never_swapped(pg_conn, pg_session, overrides):
    hospital_id = _seed_hospital(pg_conn)
    _seed_item(pg_conn, hospital_id, **overrides)

    assert _swap(pg_session).swapped == 0


def test_the_conditional_update_blocks_on_a_moved_revision(pg_conn, pg_session):
    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(pg_conn, hospital_id)
    item = pg_session.get(ContentItem, item_id)
    # 세션 밖에서 판이 움직인 상황 — 추적 객체를 고치면 autoflush가 먼저 써 버린다.
    pg_conn.execute(
        text("UPDATE content_items SET content_revision=99 WHERE id=:id"), {"id": item_id}
    )

    entry, label = topic_swap_fallback._swap_one(
        pg_session, item, "GENERATION_REJECTED", now=NOW
    )

    assert (entry, label) == (None, "write_conflict")


def test_the_conditional_update_blocks_on_a_moved_status(pg_conn, pg_session):
    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(pg_conn, hospital_id)
    item = pg_session.get(ContentItem, item_id)
    pg_conn.execute(
        text("UPDATE content_items SET status='CANCELLED' WHERE id=:id"), {"id": item_id}
    )  # 운영자가 취소했다

    entry, label = topic_swap_fallback._swap_one(
        pg_session, item, "GENERATION_REJECTED", now=NOW
    )

    assert (entry, label) == (None, "write_conflict")


def test_the_superseded_incident_is_the_one_the_swap_closes(
    pg_conn, pg_session, captured_recoveries
):
    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(pg_conn, hospital_id)
    incident = Incident(
        hospital_id=hospital_id,
        dedupe_key=generation_incident_dedupe_key(item_id, "GENERATION_REJECTED"),
        incident_type="CONTENT_GENERATION_FAILED",
        state="OPEN",
        severity=IncidentSeverity.HIGH,
        customer_impact="예정된 글이 아직 없습니다.",
        source_type="CONTENT_GENERATION",
        source_id=str(item_id),
        safe_error_code="GENERATION_REJECTED",
        next_action="자동 복구가 진행 중입니다.",
        admin_path="/operations",
        episode_seq=2,
    )
    pg_session.add(incident)
    pg_session.flush()

    report = _swap(pg_session)

    assert report.swapped == 1
    assert captured_recoveries == [incident.id]
    history = pg_session.get(ContentItem, item_id).topic_swap_history
    assert history[0]["superseded_incident_id"] == str(incident.id)
    assert history[0]["superseded_episode_seq"] == 2
    assert history[0]["incident_recovered"] is True
    # 교체 뒤의 같은 코드 실패는 새 key로 열린다 — 옛 건을 다시 열지 않는다.
    assert generation_incident_dedupe_key(
        item_id, "GENERATION_REJECTED", topic_swap_count=1
    ) != incident.dedupe_key


def test_reconcile_closes_an_incident_whose_slot_left_the_window(
    pg_conn, pg_session, captured_recoveries
):
    """예정일이 창 밖으로 옮겨 가도 옛 인시던트는 다음 pass가 닫는다.

    창으로 좁혀 읽으면 백로그 복구가 날짜를 미룬 슬롯의 미완료 종결이 영원히 남는다.
    """

    hospital_id = _seed_hospital(pg_conn)
    incident_id = uuid.uuid4()
    outside_the_window = SLOT + timedelta(days=30)
    item_id = _seed_item(
        pg_conn,
        hospital_id,
        scheduled_date=outside_the_window,
        topic_swap_history=(
            '[{"reason_code": "GENERATION_REJECTED", "incident_recovered": false,'
            f' "superseded_incident_id": "{incident_id}"}}]'
        ),
    )

    report = _swap(pg_session)

    assert report.swapped == 0  # 창 밖이라 교체 후보로는 잡히지 않는다
    assert captured_recoveries == [incident_id]
    history = pg_session.get(ContentItem, item_id).topic_swap_history
    assert history[0]["incident_recovered"] is True


def test_an_eligible_slot_behind_fifty_non_candidates_is_still_swapped(pg_conn, pg_session):
    """상한을 비후보가 먼저 채워도 그 뒤의 적격 슬롯이 굶지 않는다.

    SQL이 거를 수 있는 것(종착 여부·원인 코드)은 SQL에서 거르고, 거를 수 없는 것
    (모델 HARD 단정)은 정렬 키로 그 뒤를 이어 읽어 도달한다.
    """

    hospital_id = _seed_hospital(pg_conn)
    # SQL 술어가 바로 떨어뜨리는 행 — 아직 자동 복구가 소유한 상태다.
    for index in range(5):
        _seed_item(
            pg_conn,
            hospital_id,
            sequence_no=index + 1,
            summary='{"generation_attempt": {"reason": "GENERATION_REJECTED",'
            ' "retry_class": "SAMPLE_RECOVERABLE"}}',
        )
    # SQL은 통과하지만 파이썬 술어가 떨어뜨리는 행 — 한 페이지(50)를 통째로 채운다.
    for index in range(topic_swap_fallback.TOPIC_SWAP_SELECT_LIMIT):
        _seed_item(
            pg_conn,
            hospital_id,
            sequence_no=index + 100,
            summary='{"generation_attempt": {"reason": "GENERATION_REJECTED",'
            ' "retry_class": "OPERATOR_REQUIRED"},'
            ' "ai_review": {"findings": [{"severity": "HARD"}]}}',
        )
    eligible = _seed_item(pg_conn, hospital_id, sequence_no=900)

    report = _swap(pg_session)

    assert report.swapped == 1
    assert pg_session.get(ContentItem, eligible).topic_swap_history is not None


def test_the_second_pass_never_swaps_the_same_slot_again(pg_conn, pg_session):
    """교체는 한 번뿐이다 — 두 번째 소진은 사람의 일(OPERATOR_REQUIRED)로 남는다."""

    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(pg_conn, hospital_id)

    assert _swap(pg_session).swapped == 1

    # 새 주제도 소진됐다고 가정하고 같은 pass를 다시 돌린다.
    pg_conn.execute(
        text(
            "UPDATE content_items SET essence_check_summary = CAST(:summary AS jsonb) "
            "WHERE id=:id"
        ),
        {
            "id": item_id,
            "summary": '{"generation_attempt": {"reason": "GENERATION_REJECTED",'
            ' "retry_class": "OPERATOR_REQUIRED"}}',
        },
    )

    assert _swap(pg_session).swapped == 0
    row = pg_session.get(ContentItem, item_id)
    pg_session.refresh(row)
    assert len(row.topic_swap_history) == 1
    # 같은 날의 두 번째 pass는 당일 1회를 다시 주지 않는다 — 기록은 소진 그대로다.
    attempt = row.essence_check_summary["generation_attempt"]
    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert generation_retry_policy.retry_is_due(attempt, NOW) is False
