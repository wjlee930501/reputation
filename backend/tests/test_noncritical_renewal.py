"""비-Critical 일일 갱신(2026-10-15 누락 방지 5 나머지) — 예산 원장·판정 술어의 단위 확인.

1. 갱신 구간은 구간 뒤의 획득만 센다 — 세션당 작가 3·검수 1·전송 (3+1)×3을 넘지 않고, 평생 계수는
   지우지 않으며 평생·주제당 상한과 무관하다. 같은 날 다시 열어도 새 예산이 생기지 않는다.
2. 비-Critical 사다리의 끝은 갱신 대기(자동 복구 소유)이고, Critical·입력 변경·사람이 정하는 보류는
   종전 그대로다.
3. 로더와 워커는 같은 술어(`renewal_due`)로 갱신 전 슬롯을 집지 않는다.
4. 사고는 사람의 일이 아니라 RETRYING이다(종착 아님·자동 복구 소유).
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.workers import (
    generation_attempt_state as state,
)
from app.workers import (
    generation_incident_control,
    noncritical_renewal,
    tasks,
    topic_swap_fallback,
)
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    LIFETIME_WRITER_RESULT_LIMIT,
    WRITER_RESULT_LIMIT,
    GenerationBudgetExceeded,
    GenerationBudgetUnknown,
    acquisition_completed,
    fresh_generation_attempt,
    open_renewal_segment,
    read_generation_budget,
    reserve_acquisition,
)
from app.workers.generation_retry_policy import GenerationRetryClass

NOW = datetime(2026, 10, 15, 1, 0, tzinfo=UTC)  # 10:00 KST
TODAY = "2026-10-15"
SLOT = date(2026, 10, 15)


def _spend(attempt: dict, kind: str, count: int, *, prefix: str = "x") -> dict:
    for index in range(count):
        acquisition = f"{prefix}:{kind}:{index}:{len(attempt['budget']['acquisitions'])}"
        attempt = reserve_acquisition(attempt, acquisition_id=acquisition, kind=kind)
        attempt = acquisition_completed(
            attempt, acquisition_id=acquisition, transport_attempts=1, outcome="RESULT"
        )
    return attempt


def _lifetime_exhausted() -> dict:
    """세 주제를 다 쓴(교체 두 번) 슬롯의 예산 — 평생 작가 18회."""

    attempt = fresh_generation_attempt(topic_id="topic-a")
    attempt = _spend(attempt, "WRITER", WRITER_RESULT_LIMIT, prefix="a")
    attempt = state.record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")
    attempt = _spend(attempt, "WRITER", WRITER_RESULT_LIMIT, prefix="b")
    attempt = state.record_topic_swap(attempt, from_topic_id="topic-b", to_topic_id="topic-c")
    attempt = _spend(attempt, "WRITER", WRITER_RESULT_LIMIT, prefix="c")
    with pytest.raises(GenerationBudgetExceeded):
        reserve_acquisition(attempt, acquisition_id="over", kind="WRITER")
    return attempt


# ── 1. 예산 원장 ────────────────────────────────────────────────────────────────


def test_renewal_writer_constant_matches_the_session_limit():
    from app.services import content_review_feedback

    assert state.RENEWAL_WRITER_RESULTS_PER_SESSION == tasks.MAX_GENERATIONS_PER_SESSION
    assert content_review_feedback.RENEWAL_FEEDBACK_PATH == (
        GENERATION_ATTEMPT_KEY,
        state.GENERATION_BUDGET_KEY,
        state.RENEWAL_KEY,
        "feedback",
    )
    assert content_review_feedback.RENEWAL_FEEDBACK_LIMIT == state.RENEWAL_FEEDBACK_LIMIT


def test_a_renewal_segment_buys_exactly_one_session_after_the_lifetime_cap():
    attempt = _lifetime_exhausted()
    lifetime_before = read_generation_budget(attempt).writer_results
    assert lifetime_before == LIFETIME_WRITER_RESULT_LIMIT

    renewed = open_renewal_segment(attempt, period=TODAY, sessions=1, topic_id="topic-c")
    renewed = _spend(renewed, "WRITER", 3, prefix="r1")
    with pytest.raises(GenerationBudgetExceeded, match="WRITER_RESULTS_EXHAUSTED"):
        reserve_acquisition(renewed, acquisition_id="r1:writer:4", kind="WRITER")
    # 검수는 정상 세션과 같다(후보마다 한 번 — 문체 보완·HARD 삭제 재작성의 재검수 포함).
    renewed = _spend(renewed, "REVIEWER", 3, prefix="r1")
    with pytest.raises(GenerationBudgetExceeded, match="REVIEWER_RESULTS_EXHAUSTED"):
        reserve_acquisition(renewed, acquisition_id="r1:review:4", kind="REVIEWER")
    # 평생 계수는 지우지 않는다 — 그대로 늘어난다.
    budget = read_generation_budget(renewed)
    assert budget.writer_results == lifetime_before + 3
    assert budget.reviewer_results == 3
    assert state.current_topic_writers_exhausted(renewed) is True


def test_reopening_the_same_day_grants_nothing_and_a_new_day_grants_one_more_session():
    attempt = _lifetime_exhausted()
    day_one = open_renewal_segment(attempt, period=TODAY, sessions=1, topic_id="topic-c")
    day_one = _spend(day_one, "WRITER", 3, prefix="d1")
    again = open_renewal_segment(day_one, period=TODAY, sessions=1, topic_id="topic-c")
    assert again["budget"] == day_one["budget"]
    with pytest.raises(GenerationBudgetExceeded):
        reserve_acquisition(again, acquisition_id="d1:extra", kind="WRITER")

    day_two = open_renewal_segment(day_one, period="2026-10-16", sessions=1, topic_id="topic-c")
    segment = state.renewal_segment(day_two)
    assert segment["count"] == 2
    assert segment["acquisition_count"] == len(day_two["budget"]["acquisitions"])
    day_two = _spend(day_two, "WRITER", 3, prefix="d2")
    with pytest.raises(GenerationBudgetExceeded):
        reserve_acquisition(day_two, acquisition_id="d2:extra", kind="WRITER")
    assert read_generation_budget(day_two).writer_results == LIFETIME_WRITER_RESULT_LIMIT + 6


def test_reserved_but_unfinished_acquisitions_count_against_the_segment():
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    for index in range(3):
        renewed = reserve_acquisition(renewed, acquisition_id=f"held:{index}", kind="WRITER")
    with pytest.raises(GenerationBudgetExceeded):
        reserve_acquisition(renewed, acquisition_id="held:3", kind="WRITER")


def test_transport_attempts_are_bounded_by_the_segment():
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    _writers, _reviewers, text_limit = state.renewal_limits(1)
    assert text_limit == (3 + 3) * state.SEMANTIC_TRANSPORT_LIMIT
    # 작가 3회가 각자 전송 3회를 다 쓰면 검수 몫(전송 9)만 남는다.
    for index in range(3):
        acquisition = f"t:{index}"
        renewed = reserve_acquisition(renewed, acquisition_id=acquisition, kind="WRITER")
        for _ in range(state.SEMANTIC_TRANSPORT_LIMIT):
            renewed = state.transport_attempt_started(renewed, acquisition_id=acquisition)
        renewed = acquisition_completed(
            renewed, acquisition_id=acquisition, transport_attempts=3, outcome="RESULT"
        )
    for index in range(3):
        renewed = reserve_acquisition(renewed, acquisition_id=f"t:review:{index}", kind="REVIEWER")
    assert read_generation_budget(renewed).text_http_reserved == 3 * state.SEMANTIC_TRANSPORT_LIMIT
    # 전송이 바닥난 구간은 정상 세션 하나를 살 수 없으므로 그날은 끝이다(공회전 없음).
    assert state.renewal_segment_spent(renewed) is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.update(count=0),
        lambda s: s.update(sessions=0),
        lambda s: s.update(period=""),
        lambda s: s.update(acquisition_count=10_000),
        lambda s: s.update(writer_results=10_000),
        lambda s: s.update(feedback="not a list"),
        lambda s: s.update(topic_id=""),
    ],
)
def test_a_malformed_segment_is_unknown_spend_not_fresh_allowance(mutate):
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    broken = copy.deepcopy(renewed)
    mutate(broken["budget"]["renewal"])
    with pytest.raises(GenerationBudgetUnknown):
        read_generation_budget(broken)
    with pytest.raises(GenerationBudgetUnknown):
        reserve_acquisition(broken, acquisition_id="w", kind="WRITER")


def test_images_keep_the_slot_lifetime_budget_in_renewal():
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    for index in range(state.IMAGE_HTTP_ATTEMPT_LIMIT // state.SEMANTIC_TRANSPORT_LIMIT):
        renewed = reserve_acquisition(renewed, acquisition_id=f"img:{index}", kind="IMAGE")
    with pytest.raises(GenerationBudgetExceeded, match="IMAGE_HTTP_ATTEMPTS_EXHAUSTED"):
        reserve_acquisition(renewed, acquisition_id="img:last", kind="IMAGE")


def test_renewal_feedback_is_bounded_and_read_from_the_segment():
    feedback = [f"사유 {index}" for index in range(8)]
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c", feedback=feedback
    )
    summary = {GENERATION_ATTEMPT_KEY: renewed}
    assert state.renewal_feedback(summary) == feedback[-state.RENEWAL_FEEDBACK_LIMIT :]
    assert state.renewal_feedback({}) == []


# ── 2. 판정 술어 ────────────────────────────────────────────────────────────────


def _slot(attempt: dict, **fields) -> SimpleNamespace:
    values = {
        "id": "slot",
        "hospital_id": "h1",
        "scheduled_date": SLOT,
        "first_published_at": None,
        "human_edited_at": None,
        "topic_swap_history": [{}, {}],
        "query_target_id": "topic-c",
        "body": None,
        "essence_check_summary": {GENERATION_ATTEMPT_KEY: attempt},
    }
    values.update(fields)
    return SimpleNamespace(**values)


def _record(budget_attempt: dict, **fields) -> dict:
    return {**budget_attempt, **fields}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_NONCRITICAL_DAILY_RENEWAL_SESSIONS", 1)
    monkeypatch.setattr(settings, "CONTENT_AUTO_TOPIC_SWAP_MAX", 2)


@pytest.mark.parametrize(
    "fields",
    [
        {"reason": "GENERATION_REJECTED", "retry_class": "OPERATOR_REQUIRED"},
        {"reason": "CONTENT_AI_HARD_FINDING", "retry_class": "OPERATOR_REQUIRED"},
        {"reason": "GENERATION_BUDGET_EXHAUSTED", "retry_class": "SAMPLE_RECOVERABLE"},
    ],
)
def test_a_spent_non_critical_ladder_is_renewal_due(enabled, fields):
    item = _slot(_record(_lifetime_exhausted(), **fields))
    assert noncritical_renewal.renewal_candidate(item) is True
    assert noncritical_renewal.renewal_due(item, NOW) is True


@pytest.mark.parametrize(
    "fields",
    [
        # Critical: 교정 상한을 다 쓴 사실·안전 차단.
        {
            "reason": "CONTENT_AI_HARD_FINDING",
            "retry_class": "OPERATOR_REQUIRED",
            "auto_correction_exhausted": True,
        },
        # 모델이 단정한 Critical HARD·금지 표현 — 입력 변경 종착.
        {"reason": "CONTENT_AI_HARD_FINDING", "retry_class": "INPUT_CHANGE_REQUIRED"},
        {"reason": "FORBIDDEN_EXPRESSION", "retry_class": "INPUT_CHANGE_REQUIRED"},
        # 사람이 정하는 참고자료 보류.
        {"reason": "MISSING_REFERENCES", "retry_class": "OPERATOR_REQUIRED", "operator_decides": True},
        # 이미지(자기 폴백)·지출 불명(D22)·검수 장애(D37 선발행).
        {"reason": "IMAGE_GENERATION_RETRIES_EXHAUSTED", "retry_class": "OPERATOR_REQUIRED"},
        {"reason": "LEGACY_SPEND_UNKNOWN", "retry_class": "OPERATOR_REQUIRED"},
        {
            "reason": "CONTENT_AI_REVIEW_UNAVAILABLE",
            "retry_class": "OPERATOR_REQUIRED",
            "review_unavailable_cap_reached": True,
        },
        # 아직 표본 예산 안이다.
        {"reason": "GENERATION_REJECTED", "retry_class": "SAMPLE_RECOVERABLE"},
    ],
)
def test_critical_and_human_held_records_are_never_renewed(enabled, fields):
    attempt = _record(_lifetime_exhausted(), **fields)
    item = _slot(attempt)
    assert noncritical_renewal.renewal_candidate(item) is False
    assert noncritical_renewal.renewal_wait_fields(item, attempt, now=NOW) is None


def test_published_or_human_edited_slots_are_never_renewed(enabled):
    attempt = _record(_lifetime_exhausted(), reason="GENERATION_REJECTED", retry_class="OPERATOR_REQUIRED")
    assert not noncritical_renewal.renewal_candidate(_slot(attempt, first_published_at=NOW))
    assert not noncritical_renewal.renewal_candidate(_slot(attempt, human_edited_at=NOW))


def test_unknown_spend_is_left_to_the_legacy_reset(enabled):
    attempt = {"reason": "GENERATION_REJECTED", "retry_class": "OPERATOR_REQUIRED"}
    item = _slot(attempt, body="본문")
    assert noncritical_renewal.renewal_candidate(item) is False


def test_renewal_is_off_when_sessions_are_zero(monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_NONCRITICAL_DAILY_RENEWAL_SESSIONS", 0)
    attempt = _record(_lifetime_exhausted(), reason="GENERATION_REJECTED", retry_class="OPERATOR_REQUIRED")
    item = _slot(attempt)
    assert noncritical_renewal.renewal_candidate(item) is False
    assert noncritical_renewal.renewal_wait_fields(item, attempt, now=NOW) is None


def test_a_slot_with_swaps_left_keeps_the_swap_ladder(enabled):
    """교체가 남은 슬롯의 소진은 종전처럼 OPERATOR_REQUIRED로 남겨 교체 pass가 집는다."""

    attempt = fresh_generation_attempt(topic_id="topic-a")
    attempt = _spend(attempt, "WRITER", WRITER_RESULT_LIMIT)
    record = _record(attempt, reason="GENERATION_REJECTED", retry_class="OPERATOR_REQUIRED")
    item = _slot(record, topic_swap_history=None)
    assert noncritical_renewal.renewal_wait_fields(item, record, now=NOW) is None
    assert topic_swap_fallback.exhausted_body_sample_reason(item) == "GENERATION_REJECTED"


def test_the_ladder_end_becomes_a_renewal_wait_never_operator_required(enabled):
    record = _record(
        _lifetime_exhausted(),
        reason="GENERATION_REJECTED",
        retry_class="OPERATOR_REQUIRED",
        exhausted_days=3,
    )
    item = _slot(record)
    fields = noncritical_renewal.renewal_wait_fields(item, record, now=NOW)
    assert fields[noncritical_renewal.DAILY_RENEWAL_KEY] is True
    assert fields["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    assert fields["exhausted_days"] == 0
    # 오늘 구간이 아직 없으니 오늘 남은 스윕이 다음 시도다.
    due = datetime.fromisoformat(fields["next_retry_at"])
    assert NOW < due < NOW + timedelta(days=1)


def test_inside_an_open_segment_every_non_critical_failure_waits_for_tomorrow(enabled):
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    # 오늘 세션(작가 1·검수 1)을 쓴 뒤의 실패다.
    renewed = _spend(_spend(renewed, "WRITER", 1, prefix="s"), "REVIEWER", 1, prefix="s")
    for reason in ("PROVIDER_TIMEOUT", "COST_BLOCKED", "GENERATION_REJECTED", "MISSING_REFERENCES"):
        record = _record(renewed, reason=reason, retry_class="ENVIRONMENT_RECOVERABLE")
        fields = noncritical_renewal.renewal_wait_fields(_slot(record), record, now=NOW)
        assert fields is not None, reason
        due = datetime.fromisoformat(fields["next_retry_at"])
        assert due.astimezone(noncritical_renewal.KST).date() > SLOT


def test_an_empty_slot_symptom_follows_the_critical_cause_it_covers(enabled):
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    record = _record(renewed, reason="CONTENT_NOT_GENERATED", retry_class="OPERATOR_REQUIRED")
    critical_previous = {"reason": "FORBIDDEN_EXPRESSION", "retry_class": "INPUT_CHANGE_REQUIRED"}
    assert (
        noncritical_renewal.renewal_wait_fields(
            _slot(record), record, now=NOW, previous=critical_previous
        )
        is None
    )
    assert noncritical_renewal.renewal_wait_fields(_slot(record), record, now=NOW) is not None


def test_renewal_due_is_false_after_todays_segment_opened(enabled):
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    record = _record(
        renewed,
        reason="GENERATION_REJECTED",
        retry_class="SAMPLE_RECOVERABLE",
        daily_renewal=True,
    )
    item = _slot(record)
    assert noncritical_renewal.renewal_candidate(item) is True
    assert noncritical_renewal.renewal_due(item, NOW) is False
    assert noncritical_renewal.renewal_due(item, NOW + timedelta(days=1)) is True
    # 갱신 슬롯은 정규 교체 계단을 다시 타지 않는다.
    assert topic_swap_fallback.exhausted_body_sample_reason(item) is None


# ── 3. 로더·워커 합의 ───────────────────────────────────────────────────────────


def test_loader_and_worker_agree_on_a_renewal_due_slot(enabled, monkeypatch):
    philosophy = SimpleNamespace(id="p1", director_delta_ids=[])
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    monkeypatch.setattr(noncritical_renewal, "datetime", _frozen(NOW))
    record = _record(
        _lifetime_exhausted(), reason="GENERATION_REJECTED", retry_class="OPERATOR_REQUIRED"
    )
    item = _slot(record, body="막힌 본문", content_type=SimpleNamespace(value="FAQ"))

    assert tasks._generation_retry_is_eligible(object())(item) is False

    def _must_not_generate(*_a, **_k):
        raise AssertionError("must not generate before the renewal segment opens")

    monkeypatch.setattr(tasks, "_generate_single_content_item", _must_not_generate)
    recorded = []
    recorder = SimpleNamespace(record=lambda *a, **k: recorded.append((a, k)))
    state_, code, _message = tasks._run_generation_item(
        object(), recorder, item, SimpleNamespace(id="h1", name="병원")
    )
    assert state_ == tasks.GenerationItemState.SKIPPED
    assert code == noncritical_renewal.DAILY_RENEWAL_REASON
    # 종착 판정도 갱신이 소유한 비-Critical 소진을 종착으로 읽지 않는다.
    assert tasks._terminal_attempt_is_unchanged(item, philosophy) is False


def test_a_critical_exhaustion_is_still_terminal_for_loader_and_worker(enabled, monkeypatch):
    philosophy = SimpleNamespace(id="p1", director_delta_ids=[])
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    item = _slot({}, body="막힌 본문", content_type=SimpleNamespace(value="FAQ"))
    context = tasks._generation_attempt_context(item, philosophy)
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: _record(
            _lifetime_exhausted(),
            reason="CONTENT_AI_HARD_FINDING",
            retry_class="OPERATOR_REQUIRED",
            auto_correction_exhausted=True,
            context=context,
            observed_at=NOW.isoformat(),
        )
    }
    assert tasks._terminal_attempt_is_unchanged(item, philosophy) is True
    assert tasks._generation_retry_is_eligible(object())(item) is False


def _frozen(moment: datetime):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    return _Frozen


# ── 4. 사고: 사람의 일이 아니다 ─────────────────────────────────────────────────


def test_a_renewal_wait_body_repair_blocker_is_retrying_not_terminal(enabled):
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    due = (NOW + timedelta(days=1)).isoformat()
    record = _record(
        renewed,
        reason="MISSING_REFERENCES",
        retry_class="SAMPLE_RECOVERABLE",
        daily_renewal=True,
        next_retry_at=due,
    )
    item = _slot(
        record,
        body="본문",
        essence_check_summary={
            GENERATION_ATTEMPT_KEY: record,
            # 저장 본문 수리 예산(3일)은 다 썼다 — 종전이면 사람의 일이었다.
            "automatic_body_repair": {"period": TODAY, "count": 2, "exhausted_days": 3},
        },
    )
    assert generation_incident_control.generation_block_is_terminal("MISSING_REFERENCES", item) is False
    assert generation_incident_control.scheduled_recovery_owns_blocker("MISSING_REFERENCES", item)
    assert generation_incident_control.scheduled_recovery_owns_blocker("CONTENT_NOT_GENERATED", item)
    assert generation_incident_control.scheduled_recovery_deadline(
        item, "CONTENT_NOT_GENERATED"
    ) == datetime.fromisoformat(due)


def test_a_publishable_body_is_never_wiped_by_the_renewal(enabled, monkeypatch):
    """기록이 실패를 말해도 지금 본문이 게이트를 통과하면(D37 선발행 등) 갱신이 비우지 않는다."""

    record = _record(
        _lifetime_exhausted(), reason="GENERATION_REJECTED", retry_class="OPERATOR_REQUIRED"
    )
    item = _slot(record, body="발행 가능한 본문", content_revision=3, status="DRAFT")
    monkeypatch.setattr(topic_swap_fallback, "_stored_body_is_publishable", lambda *_a: True)

    def _must_not_touch(*_a, **_k):
        raise AssertionError("must not reset a publishable body")

    monkeypatch.setattr(topic_swap_fallback, "_swap_target", _must_not_touch)
    db = SimpleNamespace(execute=_must_not_touch, commit=lambda: None)
    assert topic_swap_fallback._renew_one(db, item, now=NOW) == "body_publishable"


# ── Codex 1차 검토 반영 ─────────────────────────────────────────────────────────


def test_a_second_configured_session_is_used_the_same_day(enabled, monkeypatch):
    """세션 2 설정: 첫 세션 실패 뒤 구간에 세션이 남았으면 오늘 남은 스윕이 쓴다."""

    monkeypatch.setattr(settings, "CONTENT_NONCRITICAL_DAILY_RENEWAL_SESSIONS", 2)
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=2, topic_id="topic-c"
    )
    renewed = _spend(_spend(renewed, "WRITER", 3, prefix="s1"), "REVIEWER", 1, prefix="s1")
    record = _record(renewed, reason="GENERATION_REJECTED", retry_class="SAMPLE_RECOVERABLE")
    fields = noncritical_renewal.renewal_wait_fields(_slot(record), record, now=NOW)
    due = datetime.fromisoformat(fields["next_retry_at"])
    assert due.astimezone(noncritical_renewal.KST).date() == SLOT
    renewed = _spend(_spend(renewed, "WRITER", 3, prefix="s2"), "REVIEWER", 1, prefix="s2")
    record = _record(renewed, reason="GENERATION_REJECTED", retry_class="SAMPLE_RECOVERABLE")
    fields = noncritical_renewal.renewal_wait_fields(_slot(record), record, now=NOW)
    assert datetime.fromisoformat(fields["next_retry_at"]).astimezone(
        noncritical_renewal.KST
    ).date() > SLOT


def test_a_late_pass_never_reopens_a_previous_day(enabled):
    """자정을 넘긴 늦은 pass가 어제 날짜로 다시 열지 않는다(D → D+1 → D → D+1)."""

    attempt = _lifetime_exhausted()
    day = open_renewal_segment(attempt, period="2026-10-15", sessions=1, topic_id="t")
    next_day = open_renewal_segment(day, period="2026-10-16", sessions=1, topic_id="t")
    next_day = _spend(next_day, "WRITER", 3, prefix="n")
    late = open_renewal_segment(next_day, period="2026-10-15", sessions=1, topic_id="t")
    assert late["budget"] == next_day["budget"]
    again = open_renewal_segment(late, period="2026-10-16", sessions=1, topic_id="t")
    assert again["budget"] == next_day["budget"]
    with pytest.raises(GenerationBudgetExceeded):
        reserve_acquisition(again, acquisition_id="late", kind="WRITER")
    record = _record(next_day, reason="GENERATION_REJECTED", retry_class="SAMPLE_RECOVERABLE", daily_renewal=True)
    late_now = datetime(2026, 10, 15, 14, 59, tzinfo=UTC)  # 23:59 KST 10/15
    assert noncritical_renewal.renewal_due(_slot(record), late_now) is False


@pytest.mark.parametrize(
    "fields",
    [
        # W3 3일 창이 닫힌 공급자 장애(기한 없음으로 굳음).
        {
            "reason": "PROVIDER_TIMEOUT",
            "retry_class": "ENVIRONMENT_RECOVERABLE",
            "environment_since": "2026-10-10T00:00:00+00:00",
            "next_retry_at": None,
        },
        # 게이트가 그 위에 쓴 원고 미생성 사람의 일.
        {"reason": "CONTENT_NOT_GENERATED", "retry_class": "OPERATOR_REQUIRED", "next_retry_at": None},
    ],
)
def test_a_closed_environment_window_is_renewed_not_left_to_a_human(enabled, fields):
    item = _slot(_record(_lifetime_exhausted(), **fields))
    assert noncritical_renewal.renewal_due(item, NOW) is True


def test_an_open_environment_window_keeps_its_own_retry(enabled, monkeypatch):
    # ladder_spent → environment_window_closed uses wall-clock now; pin both modules to NOW
    # so an open 3-day window is not treated as closed after the fixture date ages.
    from app.workers import generation_retry_policy

    monkeypatch.setattr(noncritical_renewal, "datetime", _frozen(NOW))
    monkeypatch.setattr(generation_retry_policy, "datetime", _frozen(NOW))
    fields = {
        "reason": "PROVIDER_TIMEOUT",
        "retry_class": "ENVIRONMENT_RECOVERABLE",
        "environment_since": NOW.isoformat(),
        "attempt_period": TODAY,
        "provider_attempt_count": 1,
        "next_retry_at": (NOW + timedelta(hours=3)).isoformat(),
    }
    assert noncritical_renewal.renewal_due(_slot(_record(fresh_generation_attempt(topic_id="t"), **fields)), NOW) is False


def test_a_critical_swap_request_in_renewal_mode_still_takes_the_w6_swap(enabled, monkeypatch):
    """갱신 중 생긴 Critical(교정 상한 소진)의 교체 요청은 남은 W6 교체를 탄다 — 교체는 구간을 닫는다."""

    attempt = fresh_generation_attempt(topic_id="topic-a")
    attempt = _spend(attempt, "WRITER", WRITER_RESULT_LIMIT)
    renewed = open_renewal_segment(attempt, period=TODAY, sessions=1, topic_id="topic-a")
    record = _record(
        renewed,
        reason="CONTENT_AI_HARD_FINDING",
        retry_class="SAMPLE_RECOVERABLE",
        auto_correction_exhausted=True,
    )
    item = _slot(record, topic_swap_history=None)
    assert topic_swap_fallback.exhausted_body_sample_reason(item) == "CONTENT_AI_HARD_FINDING"
    swapped = state.record_topic_swap(renewed, from_topic_id="topic-a", to_topic_id="topic-b")
    assert state.renewal_segment(swapped) is None
    # 비-Critical 갱신 기록은 정규 교체를 타지 않는다.
    plain = _record(renewed, reason="GENERATION_REJECTED", retry_class="OPERATOR_REQUIRED")
    assert topic_swap_fallback.exhausted_body_sample_reason(_slot(plain, topic_swap_history=None)) is None


def test_a_waiting_body_slot_is_not_reclaimed_before_the_next_renewal(enabled, monkeypatch):
    philosophy = SimpleNamespace(id="p1", director_delta_ids=[])
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    monkeypatch.setattr(noncritical_renewal, "datetime", _frozen(NOW))
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    record = _record(
        renewed,
        reason="CONTENT_AI_HARD_FINDING",
        retry_class="SAMPLE_RECOVERABLE",
        daily_renewal=True,
        next_retry_at=(NOW + timedelta(hours=16)).isoformat(),
    )
    item = _slot(record, body="막힌 본문", content_type=SimpleNamespace(value="FAQ"))
    assert noncritical_renewal.renewal_waiting(item, NOW) is True
    assert tasks._generation_retry_is_eligible(object())(item) is False
    monkeypatch.setattr(noncritical_renewal, "datetime", _frozen(NOW + timedelta(hours=17)))
    assert noncritical_renewal.renewal_waiting(item) is False


def test_out_of_session_reviews_are_bought_through_the_renewal_segment(enabled, monkeypatch):
    """교정·결정적 삭제·저장 본문 재검수도 갱신 구간의 검수 상한(1)을 넘지 못한다."""

    import asyncio

    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    item = _slot(_record(renewed, reason="DAILY_RENEWAL", retry_class="SAMPLE_RECOVERABLE"))

    class _Ledger:
        def __init__(self, _db, slot):
            self.slot = slot

        def _attempt(self):
            return self.slot.essence_check_summary[GENERATION_ATTEMPT_KEY]

        def _write(self, attempt):
            self.slot.essence_check_summary = {GENERATION_ATTEMPT_KEY: attempt}

        def reserve(self, *, acquisition_id, kind):
            self._write(reserve_acquisition(self._attempt(), acquisition_id=acquisition_id, kind=kind))
            return SimpleNamespace(acquisition_id=acquisition_id)

        def complete(self, reservation, *, transport_attempts, outcome):
            self._write(
                acquisition_completed(
                    self._attempt(),
                    acquisition_id=reservation.acquisition_id,
                    transport_attempts=transport_attempts,
                    outcome=outcome,
                )
            )

    monkeypatch.setattr(tasks, "GenerationBudgetLedger", _Ledger)
    calls = []

    async def _review(**_kwargs):
        calls.append(1)
        return "PASS"

    metered = tasks._renewal_metered(object(), item, "REVIEWER", _review)
    for _ in range(3):
        assert asyncio.run(metered()) == "PASS"
    with pytest.raises(GenerationBudgetExceeded):
        asyncio.run(metered())
    assert calls == [1, 1, 1]
    # 갱신이 아닌 슬롯은 종전 그대로(감싸지 않는다).
    plain = _slot(fresh_generation_attempt(topic_id="t"))
    assert tasks._renewal_metered(object(), plain, "REVIEWER", _review) is _review


def test_a_closed_environment_window_waits_for_renewal_even_with_swaps_left(enabled):
    """환경 장애는 교체 계단을 타지 않는다 — 창이 닫히면 사람의 사고가 아니라 곧바로 갱신 대기다."""

    record = _record(
        fresh_generation_attempt(topic_id="t"),
        reason="PROVIDER_UNAVAILABLE",
        retry_class="ENVIRONMENT_RECOVERABLE",
        environment_since="2026-10-10T00:00:00+00:00",
        next_retry_at=None,
    )
    item = _slot(record, topic_swap_history=None)
    fields = noncritical_renewal.renewal_wait_fields(item, record, now=NOW)
    assert fields is not None and fields[noncritical_renewal.DAILY_RENEWAL_KEY] is True


# ── Codex 2차 검토 반영 ─────────────────────────────────────────────────────────


def test_renewal_topic_changes_do_not_spend_the_critical_swaps(enabled):
    """일일 갱신의 주제 변경은 교체 예산이 아니다 — 남은 Critical 교체를 차감하지 않는다."""

    attempt = fresh_generation_attempt(topic_id="topic-a")
    history = [{"from_target_id": "a", "daily_renewal": True}, {"from_target_id": "b", "daily_renewal": True}]
    item = _slot(attempt, topic_swap_history=history)
    assert noncritical_renewal.regular_swap_count(history) == 0
    assert noncritical_renewal.topic_swap_budget_left(item, critical=True) is True
    history.append({"from_target_id": "c"})
    assert noncritical_renewal.regular_swap_count(history) == 1


def test_the_gate_symptom_over_a_non_renewable_record_is_never_renewed(enabled, monkeypatch):
    """원고 미생성 게이트가 입력 변경 종착을 덮어도 그 소유(갱신 제외)가 남는다."""

    philosophy = SimpleNamespace(id="p1", director_delta_ids=[])
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: None)
    stored = _record(
        _lifetime_exhausted(), reason="CONTENT_AI_HARD_FINDING", retry_class="INPUT_CHANGE_REQUIRED"
    )
    item = _slot(stored, content_type=SimpleNamespace(value="FAQ"), hospital=None)
    stored["context"] = tasks._generation_attempt_context(item, philosophy)
    db = SimpleNamespace(commit=lambda: None)
    tasks._record_gate_blocker_decision(db, item, philosophy, "CONTENT_NOT_GENERATED")
    record = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert record["reason"] == "CONTENT_NOT_GENERATED"
    assert record["retry_class"] == "OPERATOR_REQUIRED"
    assert record[noncritical_renewal.RENEWAL_EXCLUDED_KEY] is True
    assert noncritical_renewal.renewal_candidate(item) is False

    # 갱신 대상(비-Critical 소진)을 덮은 증상은 곧바로 갱신 대기다.
    renewable = _record(
        _lifetime_exhausted(), reason="GENERATION_REJECTED", retry_class="OPERATOR_REQUIRED", exhausted_days=3
    )
    renewable["context"] = stored["context"]
    other = _slot(renewable, content_type=SimpleNamespace(value="FAQ"), hospital=None)
    tasks._record_gate_blocker_decision(db, other, philosophy, "CONTENT_NOT_GENERATED")
    record = other.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert record[noncritical_renewal.DAILY_RENEWAL_KEY] is True
    assert record["retry_class"] == "SAMPLE_RECOVERABLE"


def test_a_spent_segment_on_a_body_path_records_a_renewal_wait(enabled, monkeypatch):
    """본문 경로가 갱신 구간 상한에 막히면 기록을 남겨 다음 갱신까지 다시 집지 않는다(공회전 없음)."""

    from app.workers import generation_retry_policy

    philosophy = SimpleNamespace(id="p1", director_delta_ids=[])
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: None)
    for module in (tasks, generation_retry_policy, noncritical_renewal):
        monkeypatch.setattr(module, "datetime", _frozen(NOW))
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    renewed = _spend(_spend(renewed, "WRITER", 1, prefix="s"), "REVIEWER", 1, prefix="s")
    item = _slot(
        _record(renewed, reason="DAILY_RENEWAL", retry_class="SAMPLE_RECOVERABLE", daily_renewal=True),
        body="막힌 본문",
        content_type=SimpleNamespace(value="FAQ"),
        hospital=None,
    )
    db = SimpleNamespace(commit=lambda: None)
    state_, code, _message = tasks._renewal_budget_spent(db, item, philosophy)
    assert state_ == tasks.GenerationItemState.FAILED and code == "GENERATION_BUDGET_EXHAUSTED"
    record = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert record[noncritical_renewal.DAILY_RENEWAL_KEY] is True
    assert noncritical_renewal.renewal_waiting(item, NOW) is True



# ── 오케스트레이터 결정(2026-10-14 22시) ─────────────────────────────────────────


def test_a_partial_session_ends_the_day_and_a_full_session_never_hits_the_cap(enabled):
    """정상 세션(작가 3·검수 3)은 구간 상한에 걸리지 않고, 반쪽만 남은 구간은 그날 끝이다."""

    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    assert state.renewal_segment_spent(renewed) is False
    partial = _spend(_spend(renewed, "WRITER", 1, prefix="p"), "REVIEWER", 1, prefix="p")
    assert state.renewal_segment_spent(partial) is True
    full = renewed
    for index in range(3):
        full = _spend(full, "WRITER", 1, prefix=f"f{index}")
        full = _spend(full, "REVIEWER", 1, prefix=f"f{index}")
    assert read_generation_budget(full).writer_results == LIFETIME_WRITER_RESULT_LIMIT + 3


def test_a_critical_swap_is_still_possible_after_renewal_topic_changes(enabled):
    """갱신의 주제 변경은 `renewal_topics`에만 남는다 — Critical 교체 예산(TOPIC_LIMIT)을 쓰거나 막지 않는다."""

    attempt = fresh_generation_attempt(topic_id="A")
    attempt = _spend(attempt, "WRITER", WRITER_RESULT_LIMIT, prefix="a")
    attempt = state.record_topic_swap(attempt, from_topic_id="A", to_topic_id="B")
    for previous, nxt in (("B", "C"), ("C", "D")):
        attempt = state.record_topic_swap(
            attempt, from_topic_id=previous, to_topic_id=nxt, renewal=True
        )
        attempt = open_renewal_segment(
            attempt, period=f"2026-10-1{5 if nxt == 'C' else 6}", sessions=1, topic_id=nxt
        )
    budget = read_generation_budget(attempt)
    assert budget.topics == ("A", "B") and budget.topic_swaps == 1
    assert attempt["budget"]["renewal_topics"] == ["C", "D"]
    history = [
        {"from_target_id": "A"},
        {"from_target_id": "B", "daily_renewal": True},
        {"from_target_id": "C", "daily_renewal": True},
    ]
    item = _slot(
        _record(
            attempt,
            reason="CONTENT_AI_HARD_FINDING",
            retry_class="SAMPLE_RECOVERABLE",
            auto_correction_exhausted=True,
        ),
        topic_swap_history=history,
    )
    assert noncritical_renewal.topic_swap_budget_left(item, critical=True) is True
    assert topic_swap_fallback.exhausted_body_sample_reason(item) == "CONTENT_AI_HARD_FINDING"
    swapped = state.record_topic_swap(attempt, from_topic_id="D", to_topic_id="E")
    budget = read_generation_budget(swapped)
    assert budget.topics == ("A", "B", "E") and budget.topic_swaps == 2
    assert state.renewal_segment(swapped) is None


def test_a_critical_verdict_on_the_body_is_never_renewed_or_overwritten(enabled, monkeypatch):
    """본문에 Critical 판정이 있으면 갱신 대상이 아니고, 구간 상한에 막혀도 그 판정을 Critical 계단에 넘긴다."""

    monkeypatch.setattr(settings, "CONTENT_FINDING_TIERS_ENABLED", True)
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: None)
    review = {
        "status": "REVISE",
        "blocking": True,
        "findings": [
            {"kind": "MEDICAL_SAFETY", "severity": "HARD", "critical": True, "quote": "완치됩니다.", "message": "보장"}
        ],
    }
    renewed = open_renewal_segment(
        _lifetime_exhausted(), period=TODAY, sessions=1, topic_id="topic-c"
    )
    record = _record(renewed, reason="GENERATION_REJECTED", retry_class="OPERATOR_REQUIRED")
    item = _slot(
        record,
        body="완치됩니다.",
        content_type=SimpleNamespace(value="FAQ"),
        hospital=None,
        essence_check_summary={GENERATION_ATTEMPT_KEY: record, "ai_review": review},
    )
    assert noncritical_renewal.renewal_candidate(item) is False
    assert noncritical_renewal.renewal_wait_fields(item, record, now=NOW) is None
    db = SimpleNamespace(commit=lambda: None)
    state_, code, _message = tasks._renewal_budget_spent(
        db, item, SimpleNamespace(id="p1", director_delta_ids=[])
    )
    assert code == "CONTENT_AI_HARD_FINDING"
    stored = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert stored["retry_class"] == GenerationRetryClass.INPUT_CHANGE_REQUIRED.value
    assert not stored.get(noncritical_renewal.DAILY_RENEWAL_KEY)
