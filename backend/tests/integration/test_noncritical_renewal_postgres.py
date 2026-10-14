"""비-Critical 일일 갱신의 전체 사다리 — 실제 PostgreSQL에서 교체 pass·로더·예산 원장·시도 기록을 함께 돌린다.

비-Critical 슬롯: 주제 A 소진 → 교체 → B 소진 → 교체 → C 소진(평생 작가 18) → 갱신 대기 →
갱신 1일차(남은 질문 D로) → 2일차·3일차(같은 주제, 누적 거절 사유) … 어느 단계도 OPERATOR_REQUIRED가
아니고, 하루에 사는 작가 결과는 구간 상한(3)을 넘지 않으며, 같은 날 두 번째 pass·스윕은 아무것도
사지 않는다. Critical 슬롯은 종전 사다리 그대로 OPERATOR_REQUIRED에 남는다.
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.content import ContentItem, ContentStatus
from app.workers import (
    generation_incident_control,
    generation_retry_policy,
    nightly_generation_batch,
    noncritical_renewal,
    tasks,
    topic_swap_fallback,
)
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    LIFETIME_WRITER_RESULT_LIMIT,
    WRITER_RESULT_LIMIT,
    GenerationBudgetExceeded,
    GenerationBudgetLedger,
    acquisition_completed,
    fresh_generation_attempt,
    read_generation_budget,
    record_topic_swap,
    renewal_segment,
    reserve_acquisition,
)
from app.workers.generation_retry_policy import GenerationRetryClass

KST = ZoneInfo("Asia/Seoul")
SLOT = date(2026, 9, 16)
TOPICS = ("대장내시경 수면 여부", "허리디스크 초기 증상", "골다공증 검사", "당뇨병 초기 증상")


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
def renewal_settings(monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_NONCRITICAL_DAILY_RENEWAL_SESSIONS", 1)
    monkeypatch.setattr(settings, "CONTENT_AUTO_TOPIC_SWAP_MAX", 2)

    async def _recover(_incident_id):
        return True

    # 인시던트 종결은 자기 async 세션이라 테스트 트랜잭션 밖이다.
    monkeypatch.setattr(topic_swap_fallback, "_recover_incident_async", _recover)


def _freeze(monkeypatch, moment: datetime) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, nightly_generation_batch, noncritical_renewal):
        monkeypatch.setattr(module, "datetime", _Frozen)


def _seed(conn, *, summary: str, history: list | None = None) -> tuple[uuid.UUID, uuid.UUID]:
    hospital_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status, site_live) "
            "VALUES (:id, '일일갱신의원', :slug, 'ACTIVE', true)"
        ),
        {"id": hospital_id, "slug": f"renewal-{uuid.uuid4().hex[:8]}"},
    )
    target_ids = []
    for name in TOPICS:
        target_id = uuid.uuid4()
        target_ids.append(target_id)
        conn.execute(
            text(
                "INSERT INTO ai_query_targets (id, hospital_id, name, target_intent, "
                "region_terms, decision_criteria, platforms, competitor_names, "
                "condition_or_symptom, patient_language, priority, status) VALUES "
                "(:id, :hospital_id, :name, 'INFORMATION', '[]', '[]', '[]', '[]', "
                ":name, 'ko', 'NORMAL', 'ACTIVE')"
            ),
            {"id": target_id, "hospital_id": hospital_id, "name": name},
        )
    schedule_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO content_schedules (id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hospital_id, 'PLAN_12', '[1]'::json, DATE '2026-09-01')"
        ),
        {"id": schedule_id, "hospital_id": hospital_id},
    )
    item_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO content_items (id, hospital_id, schedule_id, query_target_id, "
            "content_type, sequence_no, total_count, scheduled_date, status, "
            "essence_check_summary, topic_swap_history, content_revision) VALUES "
            "(:id, :hospital_id, :schedule_id, :target, 'FAQ', 1, 12, :slot, :status, "
            "CAST(:summary AS jsonb), CAST(:history AS jsonb), 1)"
        ),
        {
            "id": item_id,
            "hospital_id": hospital_id,
            "schedule_id": schedule_id,
            "target": target_ids[0],
            "slot": SLOT,
            "status": ContentStatus.DRAFT.value,
            "summary": summary,
            "history": json.dumps(history) if history is not None else None,
        },
    )
    return item_id, target_ids[0]


def _row(session, item_id) -> ContentItem:
    row = session.get(ContentItem, item_id)
    session.refresh(row)
    return row


def _attempt(session, item_id) -> dict:
    return dict(_row(session, item_id).essence_check_summary[GENERATION_ATTEMPT_KEY])


def _pass(session, moment: datetime) -> topic_swap_fallback.SwapReport:
    return topic_swap_fallback.swap_exhausted_topics(
        session,
        window_start=moment.date() - timedelta(days=7),
        window_end=moment.date() + timedelta(days=2),
        now=moment.astimezone(UTC),
    )


def _load(session, moment: datetime) -> set[uuid.UUID]:
    claimed, _truncated, _complete = tasks._load_nightly_generation_batch(
        session,
        moment.date() - timedelta(days=7),
        moment.date() + timedelta(days=2),
        is_eligible=tasks._generation_retry_is_eligible(session),
    )
    return {item.id for item in claimed}


def _release_claim(session, item_id) -> None:
    session.execute(
        update(ContentItem)
        .where(ContentItem.id == item_id)
        .values(generation_claimed_at=None, generation_claim_token=None)
    )
    session.commit()


def _buy_until_exhausted(session, item_id) -> int:
    """작가 결과를 원장이 막을 때까지 산다 — 이 슬롯이 지금 살 수 있는 수."""

    ledger = GenerationBudgetLedger(session, _row(session, item_id))
    bought = 0
    while True:
        try:
            reservation = ledger.reserve(acquisition_id=f"w:{uuid.uuid4()}", kind="WRITER")
        except GenerationBudgetExceeded:
            return bought
        ledger.complete(
            reservation, transport_attempts=1, outcome="RESULT", payload={"title": "후보"}
        )
        bought += 1
        assert bought <= LIFETIME_WRITER_RESULT_LIMIT, "원장이 막지 않았다"


def _record_failure(session, item_id, reason: str, **kwargs) -> dict:
    return tasks._remember_generation_attempt(
        session, _row(session, item_id), None, reason, **kwargs
    )


def test_a_non_critical_slot_swaps_twice_then_renews_daily_and_never_needs_a_human(
    pg_conn, pg_session, monkeypatch
):
    day1 = datetime(2026, 9, 16, 1, 0, 5, tzinfo=KST)
    item_id, first_target = _seed(
        pg_conn, summary=json.dumps({GENERATION_ATTEMPT_KEY: fresh_generation_attempt()})
    )
    _freeze(monkeypatch, day1)

    # ── 교체 두 번(누락 방지 5) ─────────────────────────────────────────────────
    for swap_number in (1, 2):
        assert _buy_until_exhausted(pg_session, item_id) == WRITER_RESULT_LIMIT
        record = _record_failure(pg_session, item_id, "GENERATION_BUDGET_EXHAUSTED")
        assert record["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
        assert not record.get(noncritical_renewal.DAILY_RENEWAL_KEY)
        report = _pass(pg_session, day1)
        assert report.swapped == 1, swap_number
        assert report.renewed == report.renewed_new_topic == 0
    assert len(_row(pg_session, item_id).topic_swap_history) == 2

    # ── 세 번째 주제도 소진 — 평생 상한(18) ─────────────────────────────────────
    assert _buy_until_exhausted(pg_session, item_id) == WRITER_RESULT_LIMIT
    budget = read_generation_budget(_attempt(pg_session, item_id))
    assert budget.writer_results == LIFETIME_WRITER_RESULT_LIMIT
    record = _record_failure(pg_session, item_id, "GENERATION_BUDGET_EXHAUSTED")
    # 종전이면 사흘 공회전 뒤 OPERATOR_REQUIRED였다. 이제는 갱신 대기(자동 복구 소유)다.
    assert record[noncritical_renewal.DAILY_RENEWAL_KEY] is True
    assert record["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    row = _row(pg_session, item_id)
    assert generation_incident_control.generation_block_is_terminal(
        "GENERATION_BUDGET_EXHAUSTED", row
    ) is False
    assert generation_incident_control.scheduled_recovery_owns_blocker(
        "GENERATION_BUDGET_EXHAUSTED", row
    ) is True
    # 갱신 단계가 구간을 열기 전에는 로더가 집지 않는다(워커와 같은 술어).
    assert noncritical_renewal.renewal_due(row, day1.astimezone(UTC)) is True
    assert item_id not in _load(pg_session, day1)

    # ── 갱신 1일차: 쓰지 않은 질문(D)이 있어 그 주제로 ───────────────────────────
    lifetime = LIFETIME_WRITER_RESULT_LIMIT
    used_targets = {
        first_target,
        *(
            uuid.UUID(entry["to_target_id"])
            for entry in _row(pg_session, item_id).topic_swap_history
        ),
    }
    report = _pass(pg_session, day1)
    assert (report.swapped, report.renewed_new_topic, report.renewed) == (0, 1, 0)
    row = _row(pg_session, item_id)
    assert row.query_target_id not in used_targets
    assert row.topic_swap_history[-1]["daily_renewal"] is True
    attempt = _attempt(pg_session, item_id)
    assert attempt["reason"] == noncritical_renewal.DAILY_RENEWAL_REASON
    assert renewal_segment(attempt)["count"] == 1
    assert item_id in _load(pg_session, day1)
    assert _buy_until_exhausted(pg_session, item_id) == 3
    lifetime += 3
    _record_failure(
        pg_session,
        item_id,
        "GENERATION_REJECTED",
        message="가격·지역·검색 구조 자동 검수 게이트가 재작성 후에도 통과되지 않았습니다.",
    )
    _release_claim(pg_session, item_id)
    record = _attempt(pg_session, item_id)
    assert record[noncritical_renewal.DAILY_RENEWAL_KEY] is True
    tomorrow_due = datetime.fromisoformat(record["next_retry_at"])
    assert tomorrow_due.astimezone(KST).date() == day1.date() + timedelta(days=1)

    # 같은 날의 다음 pass·스윕은 아무것도 사지 않는다(구간은 하루 한 번).
    later = day1.replace(hour=12)
    _freeze(monkeypatch, later)
    report = _pass(pg_session, later)
    assert (report.swapped, report.renewed, report.renewed_new_topic) == (0, 0, 0)
    assert item_id not in _load(pg_session, later)
    assert _buy_until_exhausted(pg_session, item_id) == 0

    # ── 갱신 2·3일차: 남은 질문이 없어 같은 주제를 누적 사유와 함께 ──────────────────
    for day_number in (2, 3):
        moment = day1 + timedelta(days=day_number - 1)
        _freeze(monkeypatch, moment)
        target_before = _row(pg_session, item_id).query_target_id
        report = _pass(pg_session, moment)
        assert (report.renewed, report.renewed_new_topic) == (1, 0), day_number
        row = _row(pg_session, item_id)
        assert row.query_target_id == target_before
        attempt = _attempt(pg_session, item_id)
        segment = renewal_segment(attempt)
        assert segment["count"] == day_number
        assert segment["period"] == moment.date().isoformat()
        assert segment["feedback"], "같은 주제는 지난 거절 사유를 싣는다"
        assert item_id in _load(pg_session, moment)
        assert _buy_until_exhausted(pg_session, item_id) == 3
        lifetime += 3
        record = _record_failure(pg_session, item_id, "GENERATION_REJECTED")
        assert record["retry_class"] != GenerationRetryClass.OPERATOR_REQUIRED.value
        assert record[noncritical_renewal.DAILY_RENEWAL_KEY] is True
        _release_claim(pg_session, item_id)

    budget = read_generation_budget(_attempt(pg_session, item_id))
    # 평생 계수는 지우지 않는다 — 18 + 하루 3씩.
    assert budget.writer_results == lifetime == LIFETIME_WRITER_RESULT_LIMIT + 9


def _lifetime_exhausted_attempt() -> dict:
    attempt = fresh_generation_attempt(topic_id="topic-a")

    def spend(value: dict, prefix: str) -> dict:
        for index in range(WRITER_RESULT_LIMIT):
            acquisition = f"{prefix}:{index}"
            value = reserve_acquisition(value, acquisition_id=acquisition, kind="WRITER")
            value = acquisition_completed(
                value, acquisition_id=acquisition, transport_attempts=1, outcome="RESULT"
            )
        return value

    attempt = spend(attempt, "a")
    attempt = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")
    attempt = spend(attempt, "b")
    attempt = record_topic_swap(attempt, from_topic_id="topic-b", to_topic_id="topic-c")
    return spend(attempt, "c")


@pytest.mark.parametrize(
    "fields",
    [
        # Critical: 교정 상한을 다 쓴 사실·안전 차단(교체 두 번 뒤).
        {
            "reason": "CONTENT_AI_HARD_FINDING",
            "retry_class": "OPERATOR_REQUIRED",
            "auto_correction_exhausted": True,
            "next_retry_at": None,
        },
        # 사람이 정하는 참고자료 보류.
        {
            "reason": "MISSING_REFERENCES",
            "retry_class": "OPERATOR_REQUIRED",
            "operator_decides": True,
            "next_retry_at": None,
        },
    ],
)
def test_critical_and_human_held_slots_keep_their_ladder(
    pg_conn, pg_session, monkeypatch, fields
):
    moment = datetime(2026, 9, 16, 1, 0, 5, tzinfo=KST)
    _freeze(monkeypatch, moment)
    attempt = {**_lifetime_exhausted_attempt(), **fields}
    item_id, _target = _seed(
        pg_conn,
        summary=json.dumps({GENERATION_ATTEMPT_KEY: attempt}),
        history=[{"from_target_id": None}, {"from_target_id": None}],
    )
    # 판정 때의 입력 그대로다(입력이 바뀌면 종전처럼 한 번 더 집는 별개의 규칙이다).
    row = _row(pg_session, item_id)
    attempt["context"] = tasks._generation_attempt_context(row, None)
    attempt["observed_at"] = moment.astimezone(UTC).isoformat()
    row.essence_check_summary = {GENERATION_ATTEMPT_KEY: attempt}
    pg_session.commit()

    report = _pass(pg_session, moment)

    assert (report.swapped, report.renewed, report.renewed_new_topic) == (0, 0, 0)
    stored = _attempt(pg_session, item_id)
    assert stored["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert not stored.get(noncritical_renewal.DAILY_RENEWAL_KEY)
    assert item_id not in _load(pg_session, moment)
    # Critical 계단의 끝(교정 상한 소진의 사람의 일)을 다시 써도 갱신 대기로 바뀌지 않는다.
    if fields["reason"] == "CONTENT_AI_HARD_FINDING":
        record = _record_failure(
            pg_session,
            item_id,
            "CONTENT_AI_HARD_FINDING",
            count_attempt=False,
            extra={
                "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
                "auto_correction_exhausted": True,
                "next_retry_at": None,
            },
        )
        assert record["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
        assert not record.get(noncritical_renewal.DAILY_RENEWAL_KEY)
