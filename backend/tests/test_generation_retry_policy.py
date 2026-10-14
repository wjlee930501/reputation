import json
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.config import settings
from app.services.post_publish_review_policy import AUTO_PUBLISH_CATCHUP_DAYS
from app.workers.generation_attempt_state import (
    LIFETIME_WRITER_RESULT_LIMIT,
    TEXT_HTTP_ATTEMPT_LIMIT,
    TOPIC_LIMIT,
    WRITER_RESULT_LIMIT,
    GenerationBudgetExceeded,
    GenerationBudgetUnknown,
    _topic_boundary,
    acquisition_completed,
    current_topic_writers_exhausted,
    fresh_generation_attempt,
    read_generation_budget,
    record_topic_swap,
    replace_unknown_legacy_budget,
    reserve_acquisition,
    transport_attempt_started,
    writer_acquisition_prefix,
)
from app.workers.generation_retry_policy import (
    BODY_REPAIR_DAILY_BUDGET,
    ENVIRONMENT_ATTEMPT_BUDGET,
    KST,
    RECOVERY_SWEEP_CATCHUP_DAYS,
    SAMPLE_BODY_DAILY_BUDGET,
    SAMPLE_BODY_URGENT_EXTRA,
    SAMPLE_EXHAUSTED_DAY_LIMIT,
    SAMPLE_IMAGE_DAILY_BUDGET,
    GenerationRetryClass,
    environment_attempt_period,
    generation_lookahead_days,
    has_model_declared_hard_finding,
    next_recovery_deadline,
    next_recovery_sweep,
    recovery_is_abandoned,
    repair_session_is_available,
    retry_class_for,
    retry_is_due,
    sample_budget_spent,
    sample_daily_budget,
    spend_repair_session,
    sweep_claims_slot,
)


def test_lifetime_budget_reserves_transport_before_writer_result() -> None:
    # Given: a genuinely new slot with a known-zero, versioned lifetime budget.
    attempt = fresh_generation_attempt(topic_id="topic-a")

    # When: one writer acquisition is reserved and then completes after two HTTP attempts.
    reserved = reserve_acquisition(attempt, acquisition_id="writer-1", kind="WRITER")
    completed = acquisition_completed(
        reserved,
        acquisition_id="writer-1",
        transport_attempts=2,
        outcome="RESULT",
    )

    # Then: the semantic result and actual transport attempts are separately durable.
    budget = read_generation_budget(completed)
    assert budget.writer_results == 1
    assert budget.reviewer_results == 0
    assert budget.text_http_attempts == 2
    assert budget.text_http_reserved == 0


def test_lifetime_budget_counts_crashed_reservation_conservatively() -> None:
    # Given: a reservation committed before a provider call, followed by a worker crash.
    attempt = fresh_generation_attempt(topic_id="topic-a")
    crashed = reserve_acquisition(attempt, acquisition_id="writer-crashed", kind="WRITER")

    # When/Then: restart sees all three possible transport attempts as consumed headroom.
    budget = read_generation_budget(crashed)
    assert budget.text_http_attempts == 0
    assert budget.text_http_reserved == 3


def test_lifetime_budget_rejects_unknown_legacy_spend() -> None:
    # Given: an old slot with no counter that proves how much was bought.
    legacy_attempt = {"reason": "GENERATION_FAILED"}

    # When/Then: it receives no fresh allowance automatically.
    with pytest.raises(GenerationBudgetUnknown):
        reserve_acquisition(legacy_attempt, acquisition_id="writer-1", kind="WRITER")


@pytest.mark.parametrize(
    "malformation",
    [
        "partial",
        "missing_counter",
        "string_counter",
        "bool_counter",
        "negative_counter",
        "topics_not_list",
        "topic_not_string",
        "duplicate_topics",
        "acquisitions_not_list",
        "acquisition_not_object",
        "acquisition_missing_fields",
        "completed_acquisition_counter_mismatch",
        "reserved_acquisition_counter_mismatch",
        "known_counters_without_baseline",
        "legacy_baseline_counter_mismatch",
        "reset_record_not_object",
    ],
)
def test_versioned_budget_malformed_fields_never_mint_allowance(malformation: str) -> None:
    attempt = fresh_generation_attempt(topic_id="topic-a")
    budget = attempt["budget"]
    if malformation == "partial":
        attempt["budget"] = {"version": 2}
    elif malformation == "missing_counter":
        del budget["writer_results"]
    elif malformation == "string_counter":
        budget["writer_results"] = "0"
    elif malformation == "bool_counter":
        budget["writer_results"] = False
    elif malformation == "negative_counter":
        budget["reviewer_results"] = -1
    elif malformation == "topics_not_list":
        budget["topics"] = "topic-a"
    elif malformation == "topic_not_string":
        budget["topics"] = [1]
    elif malformation == "duplicate_topics":
        budget["topics"] = ["topic-a", "topic-a"]
    elif malformation == "acquisitions_not_list":
        budget["acquisitions"] = "bad"
    elif malformation == "acquisition_not_object":
        budget["acquisitions"] = ["bad"]
    elif malformation == "acquisition_missing_fields":
        budget["acquisitions"] = [{"id": "writer-old"}]
    elif malformation == "completed_acquisition_counter_mismatch":
        budget["acquisitions"] = [
            {
                "id": "writer-old",
                "kind": "WRITER",
                "status": "RESULT",
                "transport_reserved": 3,
                "transport_attempts": 1,
                "reserved_at": "2026-10-09T00:00:00+00:00",
                "payload": {"title": "already paid"},
            }
        ]
    elif malformation == "reserved_acquisition_counter_mismatch":
        budget["acquisitions"] = [
            {
                "id": "writer-old",
                "kind": "WRITER",
                "status": "RESERVED",
                "transport_reserved": 3,
                "reserved_at": "2026-10-09T00:00:00+00:00",
            }
        ]
    elif malformation == "known_counters_without_baseline":
        budget["legacy_state"] = "KNOWN_COUNTERS"
    elif malformation == "legacy_baseline_counter_mismatch":
        budget["legacy_state"] = "KNOWN_COUNTERS"
        budget["writer_results"] = 1
        budget["legacy_baseline"] = {
            "source": "LEGACY_COUNTERS",
            "writer_results": 0,
            "reviewer_results": 0,
            "text_http_attempts": 0,
            "image_http_attempts": 0,
        }
    elif malformation == "reset_record_not_object":
        budget["reset_record"] = "bad"

    with pytest.raises(GenerationBudgetUnknown, match="LEGACY_SPEND_UNKNOWN"):
        reserve_acquisition(attempt, acquisition_id="writer-new", kind="WRITER")


def test_genuine_fresh_versioned_budget_still_reserves_normally() -> None:
    attempt = fresh_generation_attempt(topic_id="topic-a")

    reserved = reserve_acquisition(
        attempt, acquisition_id="writer-new", kind="WRITER"
    )

    assert reserved["budget"]["text_http_reserved"] == 3
    assert reserved["budget"]["acquisitions"][0]["status"] == "RESERVED"


def test_known_legacy_counter_survives_reserve_transport_and_completion() -> None:
    legacy = {
        "reason": "GENERATION_REJECTED",
        "provider_attempt_count": 1,
    }

    reserved = reserve_acquisition(
        legacy, acquisition_id="writer-after-legacy", kind="WRITER"
    )
    started = transport_attempt_started(
        reserved, acquisition_id="writer-after-legacy"
    )
    completed = acquisition_completed(
        started,
        acquisition_id="writer-after-legacy",
        transport_attempts=1,
        outcome="RESULT",
    )

    budget = read_generation_budget(completed)
    assert budget.legacy_state == "KNOWN_COUNTERS"
    assert (budget.writer_results, budget.reviewer_results) == (2, 1)
    assert budget.text_http_attempts == 2
    assert budget.text_http_reserved == 0


def test_malformed_versioned_budget_can_be_replaced_once_with_full_audit_snapshot() -> None:
    malformed = {
        "reason": "GENERATION_FAILED",
        "budget": {"version": 2, "writer_results": "corrupt"},
    }

    replaced = replace_unknown_legacy_budget(
        malformed,
        actor="admin@example.test",
        reason="Verified unknown historical provider spend",
        idempotency_key="reset-malformed-v2",
        replaced_at=datetime(2026, 10, 9, tzinfo=UTC),
    )

    snapshot = read_generation_budget(replaced)
    assert snapshot.legacy_state == "REPLACED"
    assert snapshot.reset_record is not None
    assert snapshot.reset_record["previous_snapshot"] == malformed
    with pytest.raises(GenerationBudgetExceeded, match="ALREADY_REPLACED"):
        replace_unknown_legacy_budget(
            replaced,
            actor="admin@example.test",
            reason="second attempt",
            idempotency_key="reset-malformed-v2-again",
        )


def test_corrupted_replacement_record_cannot_be_reset_again() -> None:
    replaced = replace_unknown_legacy_budget(
        {"reason": "GENERATION_FAILED"},
        actor="admin@example.test",
        reason="Verified unknown historical provider spend",
        idempotency_key="reset-legacy",
    )
    replaced["budget"]["writer_results"] = "corrupt"

    with pytest.raises(GenerationBudgetExceeded, match="ALREADY_REPLACED"):
        replace_unknown_legacy_budget(
            replaced,
            actor="admin@example.test",
            reason="second attempt",
            idempotency_key="reset-legacy-again",
        )


def test_lifetime_budget_stops_writer_and_total_http_ceiling() -> None:
    # Given: six completed writer results using all 18 possible writer transports.
    attempt = fresh_generation_attempt(topic_id="topic-a")
    for index in range(6):
        acquisition_id = f"writer-{index}"
        attempt = reserve_acquisition(
            attempt, acquisition_id=acquisition_id, kind="WRITER"
        )
        attempt = acquisition_completed(
            attempt,
            acquisition_id=acquisition_id,
            transport_attempts=3,
            outcome="RESULT",
        )

    # When/Then: a seventh writer result cannot be purchased on another day or restart.
    with pytest.raises(GenerationBudgetExceeded):
        reserve_acquisition(attempt, acquisition_id="writer-7", kind="WRITER")


def test_lifetime_budget_allows_at_most_thirty_six_text_review_transports() -> None:
    attempt = fresh_generation_attempt(topic_id="topic-a")
    for kind in ("WRITER", "REVIEWER"):
        for index in range(6):
            acquisition_id = f"{kind.lower()}-{index}"
            attempt = reserve_acquisition(
                attempt, acquisition_id=acquisition_id, kind=kind
            )
            attempt = acquisition_completed(
                attempt,
                acquisition_id=acquisition_id,
                transport_attempts=3,
                outcome="RESULT",
            )

    budget = read_generation_budget(attempt)
    assert (budget.writer_results, budget.reviewer_results) == (6, 6)
    assert budget.text_http_attempts == 36
    with pytest.raises(GenerationBudgetExceeded, match="REVIEWER_RESULTS"):
        reserve_acquisition(attempt, acquisition_id="reviewer-7", kind="REVIEWER")


def test_timeout_is_a_conservatively_consumed_unknown_transport() -> None:
    attempt = fresh_generation_attempt(topic_id="topic-a")
    attempt = reserve_acquisition(
        attempt, acquisition_id="writer-timeout", kind="WRITER"
    )
    attempt = acquisition_completed(
        attempt,
        acquisition_id="writer-timeout",
        transport_attempts=1,
        outcome="UNKNOWN",
    )

    budget = read_generation_budget(attempt)
    assert budget.text_http_attempts == 1
    assert budget.writer_results == 0
    acquisition = next(
        entry
        for entry in attempt["budget"]["acquisitions"]
        if entry["id"] == "writer-timeout"
    )
    assert acquisition["status"] == "UNKNOWN"


def test_precall_transport_checkpoint_survives_zero_attempt_exception_summary() -> None:
    attempt = fresh_generation_attempt(topic_id="topic-a")
    attempt = reserve_acquisition(
        attempt, acquisition_id="image-timeout", kind="IMAGE"
    )
    attempt = transport_attempt_started(attempt, acquisition_id="image-timeout")

    completed = acquisition_completed(
        attempt,
        acquisition_id="image-timeout",
        transport_attempts=0,
        outcome="UNKNOWN",
    )

    budget = read_generation_budget(completed)
    assert budget.image_http_attempts == 1
    acquisition = completed["budget"]["acquisitions"][0]
    assert acquisition["transport_attempts"] == 1


def test_environment_failure_retries_at_an_actual_scheduled_sweep() -> None:
    observed = datetime(2026, 9, 7, 15, 30, tzinfo=UTC)  # 00:30 KST
    due = next_recovery_sweep(observed)

    assert due == datetime(2026, 9, 7, 16, 0, tzinfo=UTC)  # 01:00 KST
    attempt = {
        "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
        "attempt_count": 1,
        "next_retry_at": due.isoformat(),
    }
    assert retry_is_due(attempt, due - timedelta(seconds=1)) is False
    assert retry_is_due(attempt, due) is True


def test_input_failure_does_not_loop_on_time_alone() -> None:
    assert retry_class_for("FORBIDDEN_EXPRESSION") == GenerationRetryClass.INPUT_CHANGE_REQUIRED
    assert retry_is_due(
        {
            "retry_class": GenerationRetryClass.INPUT_CHANGE_REQUIRED.value,
            "attempt_count": 1,
            "next_retry_at": datetime.now(UTC).isoformat(),
        }
    ) is False


def test_cost_deferral_does_not_exhaust_provider_attempt_budget() -> None:
    now = datetime.now(UTC)
    assert retry_is_due(
        {
            "reason": "COST_BLOCKED",
            "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
            "attempt_count": 99,
            "next_retry_at": (now - timedelta(seconds=1)).isoformat(),
        },
        now,
    )


def test_content_review_budget_reopens_once_on_next_kst_day() -> None:
    previous_day = datetime(2026, 9, 11, 14, 0, tzinfo=UTC)
    next_sweep = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
    attempt = {
        "reason": "CONTENT_AI_REVIEW_UNAVAILABLE",
        "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
        "provider_attempt_count": 4,
        "attempt_period": "2026-09-11",
        "observed_at": previous_day.isoformat(),
        "next_retry_at": next_sweep.isoformat(),
    }
    assert retry_is_due(attempt, previous_day + timedelta(minutes=30)) is False
    assert retry_is_due(attempt, next_sweep) is True


def test_image_budget_does_not_reset_on_day_boundary() -> None:
    next_sweep = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
    assert retry_is_due(
        {
            "reason": "IMAGE_GENERATION_FAILED",
            "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
            "provider_attempt_count": 4,
            "attempt_period": "2026-09-11",
            "next_retry_at": next_sweep.isoformat(),
        },
        next_sweep,
    ) is False


def test_stochastic_failures_are_sample_recoverable_not_input_change() -> None:
    """LLM 표본 실패는 입력 변경 필요가 아니다 — 유한 예산의 재시도 대상이다."""

    for code in (
        "GENERATION_REJECTED",
        "CONTENT_IMAGE_POLICY_REJECTED",
        "IMAGE_GENERATION_FAILED",
        "IMAGE_GENERATION_RETRIES_EXHAUSTED",
        "CONTENT_IMAGE_NOT_READY",
        "CONTENT_IMAGE_NOT_VERIFIED",
    ):
        assert retry_class_for(code) == GenerationRetryClass.SAMPLE_RECOVERABLE, code
    # 승인 자료·정본 필터·재검수 대기는 여전히 입력이 바뀌어야 풀린다.
    for code in (
        "MISSING_APPROVED_ESSENCE",
        "FORBIDDEN_EXPRESSION",
        "CONTENT_AI_REVIEW_STALE",
    ):
        assert retry_class_for(code) == GenerationRetryClass.INPUT_CHANGE_REQUIRED, code


def test_hard_finding_class_depends_on_who_declared_it(monkeypatch) -> None:
    from app.core.config import settings as _settings

    # 등급이 꺼져 있으면(기본) 모든 HARD가 종착이다 — main과 같다.
    off = {"findings": [{"severity": "HARD", "kind": "HOSPITAL_FACT", "message": "진료 원칙",
                         "quote": "q"}]}
    assert has_model_declared_hard_finding(off, {"body": "q 문장입니다."}) is True
    monkeypatch.setattr(_settings, "CONTENT_FINDING_TIERS_ENABLED", True)
    assert retry_class_for("CONTENT_AI_HARD_FINDING") == (
        GenerationRetryClass.SAMPLE_RECOVERABLE
    )
    assert retry_class_for("CONTENT_AI_HARD_FINDING", model_declared_hard=True) == (
        GenerationRetryClass.INPUT_CHANGE_REQUIRED
    )
    assert has_model_declared_hard_finding(
        {"findings": [{"severity": "UNCERTAIN", "kind": "MEDICAL_SAFETY"}]}
    ) is False
    assert has_model_declared_hard_finding(
        {"findings": [{"severity": "SOFT"}, {"severity": "HARD", "kind": "MEDICAL_SAFETY"}]}
    ) is True
    # W4: 본문 한 문장을 짚은 의료 내용 없는 병원 사실 HARD는 Critical이 아니다 — 종착이 아니다.
    sentence = "본원은 환자 이야기를 끝까지 듣는 것을 진료 원칙으로 삼습니다."
    fact = {"findings": [{"severity": "SOFT"}, {"severity": "HARD", "kind": "HOSPITAL_FACT",
                          "message": "승인 자료에 없는 진료 원칙", "quote": sentence}]}
    assert has_model_declared_hard_finding(fact, {"body": f"## 안내\n{sentence}"}) is False
    # 본문을 모르거나 인용이 없는 병원 사실 HARD는 Critical이다(fail closed).
    assert has_model_declared_hard_finding(fact) is True
    assert has_model_declared_hard_finding(
        {"findings": [{"severity": "HARD", "kind": "HOSPITAL_FACT"}]}
    ) is True
    assert has_model_declared_hard_finding(None) is False


def test_sample_budget_is_daily_and_resets_on_the_next_kst_day() -> None:
    day_one = datetime(2026, 9, 11, 14, 0, tzinfo=UTC)  # 2026-09-11 23:00 KST
    attempt = {
        "reason": "GENERATION_REJECTED",
        "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
        "provider_attempt_count": SAMPLE_BODY_DAILY_BUDGET,
        "exhausted_days": 1,
        "attempt_period": "2026-09-11",
        "next_retry_at": day_one.isoformat(),
    }
    assert retry_is_due(attempt, day_one + timedelta(minutes=30)) is False  # 23:30 KST
    # 01:00 KST(= 다음 KST 일)의 첫 스윕은 새 예산을 연다.
    assert retry_is_due(attempt, datetime(2026, 9, 11, 16, 0, tzinfo=UTC)) is True


def test_sample_budget_size_differs_for_body_and_image() -> None:
    now = datetime(2026, 9, 11, 3, 0, tzinfo=UTC)
    period = environment_attempt_period(now)

    def attempt(reason: str, count: int) -> dict:
        return {
            "reason": reason,
            "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
            "provider_attempt_count": count,
            "attempt_period": period,
            "next_retry_at": (now - timedelta(minutes=1)).isoformat(),
        }

    assert sample_daily_budget("GENERATION_REJECTED") == SAMPLE_BODY_DAILY_BUDGET
    assert sample_daily_budget("IMAGE_GENERATION_FAILED") == SAMPLE_IMAGE_DAILY_BUDGET
    assert retry_is_due(attempt("GENERATION_REJECTED", 1), now) is True
    assert retry_is_due(attempt("GENERATION_REJECTED", 2), now) is False
    assert retry_is_due(attempt("IMAGE_GENERATION_FAILED", 3), now) is True
    assert retry_is_due(attempt("IMAGE_GENERATION_FAILED", 4), now) is False


def test_three_exhausted_days_end_the_sample_budget() -> None:
    now = datetime(2026, 9, 11, 3, 0, tzinfo=UTC)
    assert retry_is_due(
        {
            "reason": "GENERATION_REJECTED",
            "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
            "provider_attempt_count": 0,
            "exhausted_days": SAMPLE_EXHAUSTED_DAY_LIMIT,
            "attempt_period": "2000-01-01",
        },
        now,
    ) is False


def test_sample_budget_spent_counts_one_exhausted_day_once() -> None:
    now = datetime(2026, 9, 11, 3, 0, tzinfo=UTC)
    first = sample_budget_spent(None, "GENERATION_REJECTED", now)
    assert first == (1, 0)
    stored = {
        "provider_attempt_count": first[0],
        "exhausted_days": first[1],
        "attempt_period": environment_attempt_period(now),
    }
    second = sample_budget_spent(stored, "GENERATION_REJECTED", now)
    assert second == (SAMPLE_BODY_DAILY_BUDGET, 1)
    # 날이 바뀌면 하루 예산은 초기화되고 소진 일수만 이어진다.
    stored = {
        "provider_attempt_count": second[0],
        "exhausted_days": second[1],
        "attempt_period": "2026-09-10",
    }
    assert sample_budget_spent(stored, "GENERATION_REJECTED", now) == (1, 1)


def test_body_repair_sessions_are_bounded_daily_and_over_three_days() -> None:
    now = datetime(2026, 9, 11, 3, 0, tzinfo=UTC)
    state = None
    assert repair_session_is_available(state, now) is True
    for _ in range(BODY_REPAIR_DAILY_BUDGET):
        state = spend_repair_session(state, now)
    assert repair_session_is_available(state, now) is False
    assert state["exhausted_days"] == 1
    # 다음 날에는 다시 열린다.
    next_day = now + timedelta(days=1)
    assert repair_session_is_available(state, next_day) is True
    for _ in range(BODY_REPAIR_DAILY_BUDGET):
        state = spend_repair_session(state, next_day)
    third_day = now + timedelta(days=2)
    for _ in range(BODY_REPAIR_DAILY_BUDGET):
        state = spend_repair_session(state, third_day)
    assert state["exhausted_days"] == SAMPLE_EXHAUSTED_DAY_LIMIT
    assert repair_session_is_available(state, now + timedelta(days=3)) is False
    assert state["first_observed_at"] == now.isoformat()


def _kst(year, month, day, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=KST)


def _sample_attempt(reason: str, count: int, *, day: date, exhausted_days: int = 0) -> dict:
    return {
        "reason": reason,
        "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
        "provider_attempt_count": count,
        "exhausted_days": exhausted_days,
        "attempt_period": day.isoformat(),
    }


def test_recovery_sweep_window_matches_the_publish_catchup_window() -> None:
    """복구 스윕의 창은 발행 catch-up과 같은 7일이어야 한다."""

    assert RECOVERY_SWEEP_CATCHUP_DAYS == AUTO_PUBLISH_CATCHUP_DAYS


def test_deadline_for_a_slot_two_days_out_is_the_daytime_recovery() -> None:
    now = _kst(2026, 9, 14, 10, 0)
    attempt = _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14))

    due = next_recovery_deadline(attempt, scheduled_date=date(2026, 9, 16), now=now)

    assert due == _kst(2026, 9, 14, 12, 0).astimezone(UTC)


def test_tomorrows_slot_failing_after_the_nightly_batch_waits_for_01() -> None:
    now = _kst(2026, 9, 14, 23, 30)
    attempt = _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14))

    due = next_recovery_deadline(attempt, scheduled_date=date(2026, 9, 15), now=now)

    # 23:00 배치는 `[내일, 모레]`를 보지만 이미 지났다. 내일 01:00 스윕의 창이
    # `[내일-7, 내일]`이라 이 슬롯을 집는 첫 시각이다.
    assert due == _kst(2026, 9, 15, 1, 0).astimezone(UTC)


def test_todays_slot_with_budget_left_retries_at_the_next_recovery_sweep() -> None:
    now = _kst(2026, 9, 14, 7, 30)
    attempt = _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14))

    due = next_recovery_deadline(attempt, scheduled_date=date(2026, 9, 14), now=now)

    # The noon recovery includes today and still honors the remaining daily budget.
    assert due == _kst(2026, 9, 14, 12, 0).astimezone(UTC)


def test_spent_daily_sample_budget_moves_the_deadline_to_the_next_day() -> None:
    now = _kst(2026, 9, 14, 4, 5)
    spent = _sample_attempt("GENERATION_REJECTED", SAMPLE_BODY_DAILY_BUDGET, day=date(2026, 9, 14))

    # 이틀 뒤 슬롯은 임박이 아니다 — 하루 2세션을 다 쓰면 내일이다.
    due = next_recovery_deadline(spent, scheduled_date=date(2026, 9, 16), now=now)

    assert due == _kst(2026, 9, 15, 1, 0).astimezone(UTC)


URGENT_BODY_BUDGET = SAMPLE_BODY_DAILY_BUDGET + SAMPLE_BODY_URGENT_EXTRA


@pytest.mark.parametrize(
    ("offset", "budget"),
    [
        (-3, URGENT_BODY_BUDGET),  # 지난 슬롯
        (0, URGENT_BODY_BUDGET),  # 예정일 당일
        (1, URGENT_BODY_BUDGET),  # 내일(48시간 안)
        (2, SAMPLE_BODY_DAILY_BUDGET),
        (3, SAMPLE_BODY_DAILY_BUDGET),
        (None, SAMPLE_BODY_DAILY_BUDGET),  # 예정일을 모르면 기본 예산
    ],
)
def test_urgent_slots_get_one_more_writer_session_per_day(offset, budget) -> None:
    observed = _kst(2026, 9, 14, 23, 30)
    slot = None if offset is None else date(2026, 9, 14) + timedelta(days=offset)

    assert sample_daily_budget("GENERATION_REJECTED", scheduled_date=slot, observed=observed) == budget
    # 이미지 예산은 임박과 무관하다.
    assert (
        sample_daily_budget("IMAGE_GENERATION_FAILED", scheduled_date=slot, observed=observed)
        == SAMPLE_IMAGE_DAILY_BUDGET
    )


def test_an_urgent_slot_retries_the_same_day_after_the_normal_budget() -> None:
    """예정일 당일 슬롯이 2세션을 쓰면 오늘 다음 스윕이 세 번째를 쓰고, 그것도 쓰면 내일이다."""

    now = _kst(2026, 9, 14, 4, 5)
    slot = date(2026, 9, 14)
    normal_spent = _sample_attempt("GENERATION_REJECTED", SAMPLE_BODY_DAILY_BUDGET, day=slot)
    urgent_spent = _sample_attempt("GENERATION_REJECTED", URGENT_BODY_BUDGET, day=slot)

    assert next_recovery_deadline(normal_spent, scheduled_date=slot, now=now) == _kst(
        2026, 9, 14, 7, 0
    ).astimezone(UTC)
    assert next_recovery_deadline(urgent_spent, scheduled_date=slot, now=now) == _kst(
        2026, 9, 15, 1, 0
    ).astimezone(UTC)

    # 로더·워커의 기한 판정도 같은 예산을 읽는다. 예정일을 넘기지 않으면 기본 예산(더 적게 산다)이다.
    normal_spent["next_retry_at"] = _kst(2026, 9, 14, 7, 0).isoformat()
    at_seven = _kst(2026, 9, 14, 7, 0)
    assert retry_is_due(normal_spent, at_seven, scheduled_date=slot) is True
    assert retry_is_due(normal_spent, at_seven) is False
    urgent_spent["next_retry_at"] = _kst(2026, 9, 14, 7, 0).isoformat()
    assert retry_is_due(urgent_spent, at_seven, scheduled_date=slot) is False


def test_an_urgent_day_counts_as_exhausted_only_after_the_extra_session() -> None:
    observed = _kst(2026, 9, 14, 12, 0)
    slot = date(2026, 9, 15)
    previous = _sample_attempt("GENERATION_REJECTED", SAMPLE_BODY_DAILY_BUDGET, day=date(2026, 9, 14))

    assert sample_budget_spent(
        _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14)),
        "GENERATION_REJECTED",
        observed,
        scheduled_date=slot,
    ) == (2, 0)
    assert sample_budget_spent(
        previous, "GENERATION_REJECTED", observed, scheduled_date=slot
    ) == (3, 1)
    # 임박이 아닌 슬롯은 종전처럼 2세션째에 그날을 소진으로 센다.
    assert sample_budget_spent(
        _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14)),
        "GENERATION_REJECTED",
        observed,
        scheduled_date=date(2026, 9, 18),
    ) == (2, 1)


def test_the_urgent_session_does_not_raise_the_per_topic_writer_ceiling() -> None:
    """임박 세션은 하루 예산만 늘린다 — 주제당 작가 상한(6)·평생 상한은 그대로다."""

    attempt = fresh_generation_attempt(topic_id="topic-a")
    for index in range(WRITER_RESULT_LIMIT):
        attempt = reserve_acquisition(attempt, acquisition_id=f"w-{index}", kind="WRITER")
        attempt = acquisition_completed(
            attempt, acquisition_id=f"w-{index}", transport_attempts=1, outcome="ERROR"
        )
    with pytest.raises(GenerationBudgetExceeded):
        reserve_acquisition(attempt, acquisition_id="w-urgent", kind="WRITER")
    assert LIFETIME_WRITER_RESULT_LIMIT == TOPIC_LIMIT * WRITER_RESULT_LIMIT


def test_the_lookahead_window_reaches_three_days_and_follows_the_setting(monkeypatch) -> None:
    """리드 타임 D-3: 23:00 창은 [내일, 사흘 뒤], 설정을 내리면 같은 값으로 줄어든다."""

    assert generation_lookahead_days() == 3
    tonight = _kst(2026, 9, 14, 23, 0)
    three_days_out = date(2026, 9, 17)
    assert sweep_claims_slot(tonight, three_days_out) is True
    assert sweep_claims_slot(_kst(2026, 9, 14, 12, 0), three_days_out) is True
    # 사흘 뒤 슬롯의 첫 실패도 기한이 있다(None으로 굳지 않는다).
    attempt = _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14))
    assert next_recovery_deadline(
        attempt, scheduled_date=three_days_out, now=_kst(2026, 9, 14, 10, 0)
    ) == _kst(2026, 9, 14, 12, 0).astimezone(UTC)

    monkeypatch.setattr(settings, "NIGHTLY_GENERATION_LOOKAHEAD_DAYS", 2)
    assert sweep_claims_slot(tonight, three_days_out) is False


def test_image_budget_allows_four_attempts_before_the_day_moves() -> None:
    now = _kst(2026, 9, 14, 1, 5)
    slot = date(2026, 9, 14)
    under = _sample_attempt("IMAGE_GENERATION_FAILED", SAMPLE_IMAGE_DAILY_BUDGET - 1, day=slot)
    spent = _sample_attempt("IMAGE_GENERATION_FAILED", SAMPLE_IMAGE_DAILY_BUDGET, day=slot)

    assert next_recovery_deadline(under, scheduled_date=slot, now=now) == _kst(
        2026, 9, 14, 4, 0
    ).astimezone(UTC)
    assert next_recovery_deadline(spent, scheduled_date=slot, now=now) == _kst(
        2026, 9, 15, 1, 0
    ).astimezone(UTC)


def test_yesterdays_spent_budget_resets_at_kst_midnight() -> None:
    now = _kst(2026, 9, 15, 0, 30)
    stale = _sample_attempt(
        "IMAGE_GENERATION_FAILED", SAMPLE_IMAGE_DAILY_BUDGET, day=date(2026, 9, 14)
    )

    due = next_recovery_deadline(stale, scheduled_date=date(2026, 9, 15), now=now)

    assert due == _kst(2026, 9, 15, 1, 0).astimezone(UTC)


def test_three_exhausted_days_leave_no_automatic_deadline() -> None:
    attempt = _sample_attempt(
        "GENERATION_REJECTED",
        SAMPLE_BODY_DAILY_BUDGET,
        day=date(2026, 9, 14),
        exhausted_days=SAMPLE_EXHAUSTED_DAY_LIMIT,
    )

    assert (
        next_recovery_deadline(
            attempt, scheduled_date=date(2026, 9, 14), now=_kst(2026, 9, 14, 4, 5)
        )
        is None
    )


def test_environment_budget_is_finite_unless_the_code_resets_daily() -> None:
    now = _kst(2026, 9, 14, 2, 0)
    slot = date(2026, 9, 14)

    def attempt(reason: str) -> dict:
        return {
            "reason": reason,
            "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
            "provider_attempt_count": ENVIRONMENT_ATTEMPT_BUDGET,
            "attempt_period": slot.isoformat(),
        }

    # 비용 가드 보류는 하루 초기화가 없다. 공급자 장애는 검수 장애처럼 다음 KST 일에 다시 열린다.
    assert next_recovery_deadline(attempt("COST_BLOCKED"), scheduled_date=slot, now=now) is None
    for reason in ("CONTENT_AI_REVIEW_UNAVAILABLE", "PROVIDER_TIMEOUT"):
        assert next_recovery_deadline(
            attempt(reason), scheduled_date=slot, now=now
        ) == _kst(2026, 9, 15, 1, 0).astimezone(UTC)


def test_terminal_classes_and_unreachable_slots_have_no_deadline() -> None:
    now = _kst(2026, 9, 14, 2, 0)

    for retry_class in (
        GenerationRetryClass.OPERATOR_REQUIRED,
        GenerationRetryClass.INPUT_CHANGE_REQUIRED,
    ):
        assert (
            next_recovery_deadline(
                {"reason": "GENERATION_REJECTED", "retry_class": retry_class.value},
                scheduled_date=date(2026, 9, 14),
                now=now,
            )
            is None
        )
    # 지평(14일) 밖의 미래 슬롯은 어떤 스윕도 집지 않는다.
    assert (
        next_recovery_deadline(
            _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14)),
            scheduled_date=date(2026, 12, 25),
            now=now,
        )
        is None
    )


def test_a_slot_older_than_catchup_waits_for_the_backlog_recovery() -> None:
    """스윕 창 밖의 슬롯은 22:30 백로그 복구가 소유한다 — 기한 없는 RETRYING이 아니다."""

    observed = _kst(2026, 9, 14, 2, 0)
    attempt = _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14))

    due = next_recovery_deadline(attempt, scheduled_date=date(2026, 8, 1), now=observed)

    assert due == _kst(2026, 9, 14, 23, 30).astimezone(UTC)
    # 22:30이 이미 지난 시각이면 다음 날 실행 뒤가 된다.
    assert next_recovery_deadline(
        attempt, scheduled_date=date(2026, 8, 1), now=_kst(2026, 9, 14, 23, 0)
    ) == _kst(2026, 9, 15, 23, 30).astimezone(UTC)


def test_the_oldest_catchup_day_is_handed_to_the_backlog_recovery() -> None:
    """오늘 스윕이 집은 창의 첫날은 내일 창에서 빠진다 — 기한을 잃으면 안 된다.

    01·04·07 스윕의 창은 `[오늘-7, 오늘]`이라 오늘-7에 예정된 슬롯을 실제로 claim한다.
    마지막 주간 실행이 22:00 뒤에 실패를 기록하면 남은 후보 스윕의 창은 모두 `[내일-7, …]`
    이상이라 이 슬롯을 다시 담지 못한다. 기준을 오늘 창으로 두면 그 하루가 스윕에도
    백로그 복구에도 속하지 않아 `next_retry_at`이 `None`으로 굳고, 표본 실패는 소진
    일수가 더 쌓이지 않아 주제 교체 계단조차 열리지 않는다.
    """

    observed = _kst(2026, 9, 14, 22, 5)
    slot = observed.date() - timedelta(days=RECOVERY_SWEEP_CATCHUP_DAYS)
    attempt = _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14))

    due = next_recovery_deadline(attempt, scheduled_date=slot, now=observed)

    assert due == _kst(2026, 9, 14, 23, 30).astimezone(UTC)
    attempt["next_retry_at"] = due.isoformat()
    assert recovery_is_abandoned(attempt, due) is False


def test_a_sample_slot_inside_the_sweep_windows_is_never_abandoned() -> None:
    """예산이 남은 표본 실패는 어느 시각·어느 예정일에서도 다음 단계를 가져야 한다.

    `next_retry_at`을 명시적 `None`으로 저장하면 `retry_is_due`가 영구히 거짓이 되고
    (SAMPLE 분류에는 환경 오류 같은 하루 단위 예외 경로가 없다) 로더도 워커도 그 행을
    다시 집지 않는다. 그러면 소진 일수가 3일에 닿지 못해 `OPERATOR_REQUIRED` 승격과
    주제 교체가 함께 막힌다 — 정의된 다음 단계 없는 종착이다. 본문과 이미지 표본은
    같은 기한 계산을 공유하므로 두 코드를 함께 묶는다.
    """

    today = date(2026, 9, 14)
    for reason, budget in (
        ("GENERATION_REJECTED", SAMPLE_BODY_DAILY_BUDGET),
        ("IMAGE_GENERATION_FAILED", SAMPLE_IMAGE_DAILY_BUDGET),
    ):
        for hour in range(24):
            observed = _kst(2026, 9, 14, hour, 10)
            for offset in range(-RECOVERY_SWEEP_CATCHUP_DAYS, generation_lookahead_days() + 1):
                slot = today + timedelta(days=offset)
                for spent in range(1, budget + 1):
                    case = (reason, hour, offset, spent)
                    attempt = _sample_attempt(reason, spent, day=today)
                    due = next_recovery_deadline(attempt, scheduled_date=slot, now=observed)
                    assert due is not None, case
                    attempt["next_retry_at"] = due.isoformat()
                    assert recovery_is_abandoned(attempt, due) is False, case


def test_a_stored_null_deadline_does_not_become_due_by_time_alone() -> None:
    attempt = {
        "reason": "GENERATION_REJECTED",
        "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
        "provider_attempt_count": 1,
        "attempt_period": "2026-09-14",
        "next_retry_at": None,
    }

    assert retry_is_due(attempt, _kst(2026, 9, 14, 23, 0)) is False


def test_a_new_kst_day_still_honours_the_persisted_deadline() -> None:
    """예산이 초기화됐다는 이유로 약속한 시각보다 앞당기지 않는다."""

    next_day = _kst(2026, 9, 15, 0, 30)
    spent_yesterday = _sample_attempt(
        "GENERATION_REJECTED", SAMPLE_BODY_DAILY_BUDGET, day=date(2026, 9, 14)
    )

    future = dict(spent_yesterday, next_retry_at=_kst(2026, 9, 15, 1, 0).astimezone(UTC).isoformat())
    assert retry_is_due(future, next_day) is False
    assert retry_is_due(future, _kst(2026, 9, 15, 1, 0)) is True

    # 어떤 스윕도 집지 않는다고 저장된 결정은 날이 바뀌어도 되살아나지 않는다.
    assert retry_is_due(dict(spent_yesterday, next_retry_at=None), next_day) is False


def test_a_daily_reset_environment_budget_reopens_on_the_day_it_resets() -> None:
    """어제 소진된 일일 초기화 코드는 오늘 이미 예산이 돌아왔다."""

    attempt = {
        "reason": "CONTENT_AI_REVIEW_UNAVAILABLE",
        "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
        "provider_attempt_count": ENVIRONMENT_ATTEMPT_BUDGET,
        "attempt_period": "2026-09-14",
    }

    due = next_recovery_deadline(
        attempt, scheduled_date=date(2026, 9, 15), now=_kst(2026, 9, 15, 0, 30)
    )

    assert due == _kst(2026, 9, 15, 1, 0).astimezone(UTC)


def test_a_review_outage_survives_a_stored_no_sweep_decision_on_the_next_day() -> None:
    """검수 장애가 영구 차단으로 굳지 않게 한다.

    catch-up 창 가장자리(예정일 == 오늘-7)에서 예산이 소진되면, 다음 날 어떤 스윕의
    창에도 이 슬롯이 들지 않아 `next_retry_at`이 null로 굳는다. 그 상태로 두면 검수를
    다시 사지 않으니 저장된 판정도 영원히 그대로다 — 2026-09-20 운영 사고의 형태다.
    """

    exhausted = {
        "reason": "CONTENT_AI_REVIEW_UNAVAILABLE",
        "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
        "provider_attempt_count": ENVIRONMENT_ATTEMPT_BUDGET,
        "attempt_period": "2026-09-19",
        "next_retry_at": None,
    }

    # 같은 날에는 저장된 결정 그대로 — 예산이 아직 초기화되지 않았다.
    assert retry_is_due(exhausted, _kst(2026, 9, 19, 23, 0)) is False
    assert recovery_is_abandoned(exhausted, _kst(2026, 9, 19, 23, 0)) is True
    # 다음 KST 일의 새 예산이 어제의 예측보다 나중에 내려진 결정이다.
    assert retry_is_due(exhausted, _kst(2026, 9, 20, 1, 0)) is True
    assert recovery_is_abandoned(exhausted, _kst(2026, 9, 20, 1, 0)) is False


def test_a_non_resetting_environment_budget_stays_abandoned() -> None:
    """일일 초기화가 없는 코드(비용 가드 등)의 종착은 그대로 종착이다."""

    exhausted = {
        "reason": "COST_BLOCKED",
        "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
        "provider_attempt_count": ENVIRONMENT_ATTEMPT_BUDGET,
        "attempt_period": "2026-09-19",
        "next_retry_at": None,
    }

    assert retry_is_due(exhausted, _kst(2026, 9, 20, 1, 0)) is False
    assert recovery_is_abandoned(exhausted, _kst(2026, 9, 20, 1, 0)) is True


def test_a_record_without_a_stored_decision_is_never_called_abandoned() -> None:
    # 키가 아예 없는 레거시 기록은 "집지 않는다"고 판정한 적이 없다.
    assert recovery_is_abandoned({"reason": "CONTENT_AI_REVIEW_UNAVAILABLE"}) is False
    assert recovery_is_abandoned(None) is False


def test_a_repair_codes_deadline_follows_its_session_budget() -> None:
    """수리 코드의 기한은 재시도 클래스가 아니라 수리 세션 예산에서 나온다."""

    slot = date(2026, 9, 16)
    now = _kst(2026, 9, 16, 7, 45)
    attempt = {
        "reason": "MISSING_REFERENCES",
        "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
    }

    # 오늘 세션이 남아 있다 → 오늘 남은 첫 스윕이 아니라 이 슬롯을 집는 첫 시각.
    assert next_recovery_deadline(
        attempt, scheduled_date=slot, now=now, repair_state=None
    ) == _kst(2026, 9, 16, 12, 0).astimezone(UTC)

    # 오늘 예산만 소진 → 내일.
    spent_today = {"period": "2026-09-16", "count": BODY_REPAIR_DAILY_BUDGET, "exhausted_days": 1}
    assert next_recovery_deadline(
        attempt, scheduled_date=slot, now=now, repair_state=spent_today
    ) == _kst(2026, 9, 17, 1, 0).astimezone(UTC)

    # 소진된 날이 상한만큼 쌓였다 → 자동 기한 없음.
    exhausted = {"period": "2026-09-16", "count": BODY_REPAIR_DAILY_BUDGET,
                 "exhausted_days": SAMPLE_EXHAUSTED_DAY_LIMIT}
    assert (
        next_recovery_deadline(
            attempt, scheduled_date=slot, now=now, repair_state=exhausted
        )
        is None
    )


def test_an_input_change_repair_code_has_no_automatic_deadline() -> None:
    """승인된 입력이 틀렸다는 판정은 작가 세션으로 고칠 수 없다."""

    assert (
        next_recovery_deadline(
            {
                "reason": "FORBIDDEN_EXPRESSION",
                "retry_class": GenerationRetryClass.INPUT_CHANGE_REQUIRED.value,
            },
            scheduled_date=date(2026, 9, 16),
            now=_kst(2026, 9, 16, 7, 45),
            repair_state=None,
        )
        is None
    )


# ── 주제 교체 뒤 예산(0-1) ───────────────────────────────────────────────────


def _buy(attempt, *, kind, topic, count, transports=1, start=0):
    for index in range(start, start + count):
        acquisition_id = f"{kind.lower()}:ctx:{topic}:{index}"
        attempt = reserve_acquisition(attempt, acquisition_id=acquisition_id, kind=kind)
        attempt = acquisition_completed(
            attempt,
            acquisition_id=acquisition_id,
            transport_attempts=transports,
            outcome="RESULT",
            payload={"index": index},
        )
    return attempt


def test_swapped_topic_can_buy_a_writer_after_the_old_topic_spent_all_six() -> None:
    # Given: 옛 주제가 작가 결과 6회를 다 썼다(실패 세션 2개면 첫날에 닿는다).
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    with pytest.raises(GenerationBudgetExceeded, match="WRITER_RESULTS_EXHAUSTED"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-a:7", kind="WRITER")

    # When: 슬롯 평생 한 번의 주제 교체를 기록한다.
    swapped = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")

    # Then: 새 주제의 첫 작가 호출이 실제로 예약된다.
    reserved = reserve_acquisition(
        swapped, acquisition_id="writer:ctx:topic-b:0", kind="WRITER"
    )
    assert reserved["budget"]["acquisitions"][-1]["status"] == "RESERVED"


def test_swapped_topic_has_its_own_six_and_the_slot_lifetime_stays_eighteen() -> None:
    # W6(2026-10-14 O5): 주제는 처음 것을 합쳐 셋까지, 주제마다 작가 6회를 새로 받는다.
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    attempt = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")
    attempt = _buy(attempt, kind="WRITER", topic="topic-b", count=6)
    with pytest.raises(GenerationBudgetExceeded, match="WRITER_RESULTS_EXHAUSTED"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-b:7", kind="WRITER")
    attempt = record_topic_swap(attempt, from_topic_id="topic-b", to_topic_id="topic-c")
    attempt = _buy(attempt, kind="WRITER", topic="topic-c", count=6)

    budget = read_generation_budget(attempt)
    assert budget.writer_results == LIFETIME_WRITER_RESULT_LIMIT == 18
    assert (budget.topics, budget.topic_swaps) == (("topic-a", "topic-b", "topic-c"), 2)
    with pytest.raises(GenerationBudgetExceeded, match="WRITER_RESULTS_EXHAUSTED"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-c:7", kind="WRITER")
    # 세 번째 교체는 없다 — 예산 기록이 받아 주지 않는다.
    with pytest.raises(GenerationBudgetExceeded, match="TOPIC_SWAP_EXHAUSTED"):
        record_topic_swap(attempt, from_topic_id="topic-c", to_topic_id="topic-d")


def test_second_swap_keeps_the_slot_image_transport_cap() -> None:
    attempt = fresh_generation_attempt(topic_id="topic-a")
    attempt = _buy(attempt, kind="IMAGE", topic="topic-a", count=4, transports=3)
    attempt = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")
    attempt = record_topic_swap(attempt, from_topic_id="topic-b", to_topic_id="topic-c")
    reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-c:0", kind="WRITER")
    with pytest.raises(GenerationBudgetExceeded, match="IMAGE_HTTP_ATTEMPTS_EXHAUSTED"):
        reserve_acquisition(attempt, acquisition_id="image:new", kind="IMAGE")


def test_raising_the_topic_limit_keeps_existing_records_valid() -> None:
    """상한 상향이지 리셋이 아니다 — 교체 한 번을 쓴 옛 기록이 LEGACY_SPEND_UNKNOWN이 되지 않는다."""

    # 운영 표본 모양: 옛 주제 6회 소진 → 교체 1회 → 새 주제 6회 소진(옛 상한 TOPIC_LIMIT=2 시절 기록).
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    attempt = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")
    attempt = _buy(attempt, kind="WRITER", topic="topic-b", count=6)
    stored = json.loads(json.dumps(attempt))

    budget = read_generation_budget(stored)
    assert (budget.writer_results, budget.topic_swaps, len(budget.topics)) == (12, 1, 2)
    # 그 기록에 두 번째 교체를 더할 수 있다.
    record_topic_swap(stored, from_topic_id="topic-b", to_topic_id="topic-c")


def test_topic_list_past_the_new_limit_is_still_unknown_spend() -> None:
    attempt = fresh_generation_attempt(topic_id="topic-a")
    attempt["budget"]["topics"] = ["t1", "t2", "t3", "t4"]
    with pytest.raises(GenerationBudgetUnknown):
        read_generation_budget(attempt)


def test_swap_reopens_reviewer_and_text_transport_but_not_image_transport() -> None:
    # Given: 옛 주제가 작가·검수 6회씩, 본문 전송 36회, 이미지 전송 12회를 다 썼다.
    attempt = fresh_generation_attempt(topic_id="topic-a")
    attempt = _buy(attempt, kind="WRITER", topic="topic-a", count=6, transports=3)
    attempt = _buy(attempt, kind="REVIEWER", topic="topic-a", count=6, transports=3)
    attempt = _buy(attempt, kind="IMAGE", topic="topic-a", count=4, transports=3)
    assert read_generation_budget(attempt).text_http_attempts == TEXT_HTTP_ATTEMPT_LIMIT

    swapped = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")

    reserve_acquisition(swapped, acquisition_id="writer:ctx:topic-b:0", kind="WRITER")
    reserve_acquisition(swapped, acquisition_id="reviewer:ctx:new", kind="REVIEWER")
    # 이미지는 주제가 아니라 슬롯의 지출이다 — 교체로 다시 열리지 않는다.
    with pytest.raises(GenerationBudgetExceeded, match="IMAGE_HTTP_ATTEMPTS_EXHAUSTED"):
        reserve_acquisition(swapped, acquisition_id="image:new", kind="IMAGE")


def test_swap_records_the_old_topic_spend_without_erasing_lifetime_counters() -> None:
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"),
        kind="WRITER",
        topic="topic-a",
        count=6,
        transports=2,
    )

    swapped = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")

    assert swapped["budget"]["prior_topic_spend"] == {
        "acquisition_count": 6,
        "writer_results": 6,
        "reviewer_results": 0,
        "text_http_attempts": 12,
    }
    budget = read_generation_budget(swapped)
    assert (budget.writer_results, budget.text_http_attempts, budget.topic_swaps) == (6, 12, 1)


def test_pre_fix_swapped_budget_without_marker_derives_the_new_topic_boundary() -> None:
    # Given: 이 수정 이전에 교체된 슬롯 — topic_swaps=1이지만 표시가 없다.
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    attempt["budget"]["topics"] = ["topic-a", "topic-b"]
    attempt["budget"]["topic_swaps"] = 1
    assert "prior_topic_spend" not in attempt["budget"]

    # Then: 새 주제의 작가 획득이 아직 없으면 목록 전체를 옛 주제의 지출로 본다.
    attempt = _buy(attempt, kind="WRITER", topic="topic-b", count=2)
    assert current_topic_writers_exhausted(attempt) is False
    # 새 주제가 2회를 썼으므로 4회가 더 남는다.
    attempt = _buy(attempt, kind="WRITER", topic="topic-b", count=4, start=2)
    with pytest.raises(GenerationBudgetExceeded, match="WRITER_RESULTS_EXHAUSTED"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-b:9", kind="WRITER")


def test_writer_acquisition_prefix_carries_the_topic_token_the_boundary_reads() -> None:
    # 표시 없는 옛 교체 예산의 경계(`_topic_boundary`)는 작가 획득 id 안의 `:{topic}:`를 찾는다.
    # 실제 작가 획득 id(`tasks._generate_with_auto_review`)가 이 형식을 벗어나면 여기서 깨진다.
    prefix = writer_acquisition_prefix("ctx", "topic-b", "claim-1", "fp")
    assert ":topic-b:" in prefix

    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    attempt["budget"]["topics"] = ["topic-a", "topic-b"]
    attempt["budget"]["topic_swaps"] = 1
    attempt = reserve_acquisition(attempt, acquisition_id=prefix, kind="WRITER")

    assert _topic_boundary(attempt["budget"]) == 6


def test_unswapped_budget_keeps_the_existing_six_result_ceiling() -> None:
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    assert "prior_topic_spend" not in attempt["budget"]
    with pytest.raises(GenerationBudgetExceeded, match="WRITER_RESULTS_EXHAUSTED"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-a:6", kind="WRITER")


@pytest.mark.parametrize(
    "malformation",
    ["not_object", "count_past_end", "without_swap", "string_counter", "counter_above_lifetime"],
)
def test_malformed_prior_topic_spend_never_mints_allowance(malformation: str) -> None:
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    attempt = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")
    prior = attempt["budget"]["prior_topic_spend"]
    if malformation == "not_object":
        attempt["budget"]["prior_topic_spend"] = "bad"
    elif malformation == "count_past_end":
        prior["acquisition_count"] = 7
    elif malformation == "without_swap":
        attempt["budget"]["topic_swaps"] = 0
    elif malformation == "string_counter":
        prior["writer_results"] = "6"
    elif malformation == "counter_above_lifetime":
        prior["writer_results"] = 7

    with pytest.raises(GenerationBudgetUnknown, match="LEGACY_SPEND_UNKNOWN"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-b:0", kind="WRITER")


def _slot(**fields):
    from types import SimpleNamespace

    base = {name: None for name in (
        "title", "body", "faq_question", "image_url", "image_prompt", "generated_at",
        "generation_philosophy_id", "essence_status", "body_updated_at", "published_at",
        "first_published_at", "human_edited_at", "active_revision_id", "topic_swap_history",
        "query_target_id", "essence_check_summary",
    )}
    base.update(fields)
    return SimpleNamespace(**base)


def test_never_attempted_slot_reads_as_a_known_zero_budget() -> None:
    from app.workers.generation_attempt_state import (
        budget_generation_attempt,
        never_attempted_slot,
    )

    for summary in (None, {}, {"generation_attempt": {}}, {"blocking": False}):
        item = _slot(essence_check_summary=summary)
        assert never_attempted_slot(item)
        budget = read_generation_budget(budget_generation_attempt(item))
        assert budget.legacy_state == "KNOWN_ZERO"
        assert budget.writer_results == budget.text_http_attempts == 0


@pytest.mark.parametrize(
    "trace",
    [
        {"body": "## 본문"},
        {"title": "제목"},
        {"generated_at": datetime(2026, 9, 24, tzinfo=UTC)},
        {"first_published_at": datetime(2026, 9, 24, tzinfo=UTC)},
        {"topic_swap_history": [{"from": "a"}]},
        {"essence_check_summary": {"generation_attempt": {"reason": "GENERATION_REJECTED"}}},
    ],
)
def test_slot_with_generation_trace_keeps_legacy_spend_unknown(trace) -> None:
    from app.workers.generation_attempt_state import (
        budget_generation_attempt,
        never_attempted_slot,
    )

    item = _slot(**trace)
    assert not never_attempted_slot(item)
    with pytest.raises(GenerationBudgetUnknown, match="LEGACY_SPEND_UNKNOWN"):
        read_generation_budget(budget_generation_attempt(item))
