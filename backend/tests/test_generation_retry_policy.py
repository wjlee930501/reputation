from datetime import UTC, datetime, timedelta

from app.workers.generation_retry_policy import (
    BODY_REPAIR_DAILY_BUDGET,
    SAMPLE_BODY_DAILY_BUDGET,
    SAMPLE_EXHAUSTED_DAY_LIMIT,
    SAMPLE_IMAGE_DAILY_BUDGET,
    GenerationRetryClass,
    environment_attempt_period,
    has_model_declared_hard_finding,
    next_recovery_sweep,
    repair_session_is_available,
    retry_class_for,
    retry_is_due,
    sample_budget_spent,
    sample_daily_budget,
    spend_repair_session,
)


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


def test_hard_finding_class_depends_on_who_declared_it() -> None:
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
        {"findings": [{"severity": "SOFT"}, {"severity": "HARD", "kind": "HOSPITAL_FACT"}]}
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
