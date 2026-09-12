from datetime import UTC, datetime, timedelta

from app.workers.generation_retry_policy import (
    GenerationRetryClass,
    next_recovery_sweep,
    retry_class_for,
    retry_is_due,
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
