from datetime import UTC, date, datetime, timedelta

from app.services.post_publish_review_policy import AUTO_PUBLISH_CATCHUP_DAYS
from app.workers.generation_retry_policy import (
    BODY_REPAIR_DAILY_BUDGET,
    ENVIRONMENT_ATTEMPT_BUDGET,
    KST,
    RECOVERY_SWEEP_CATCHUP_DAYS,
    SAMPLE_BODY_DAILY_BUDGET,
    SAMPLE_EXHAUSTED_DAY_LIMIT,
    SAMPLE_IMAGE_DAILY_BUDGET,
    GenerationRetryClass,
    environment_attempt_period,
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


def test_deadline_for_a_slot_two_days_out_is_tonights_nightly_batch() -> None:
    now = _kst(2026, 9, 14, 10, 0)
    attempt = _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14))

    due = next_recovery_deadline(attempt, scheduled_date=date(2026, 9, 16), now=now)

    assert due == _kst(2026, 9, 14, 23, 0).astimezone(UTC)


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

    # 예산이 남아 있어도 오늘 남은 스윕은 23:00뿐인데 그 창에는 오늘이 없다.
    assert due == _kst(2026, 9, 15, 1, 0).astimezone(UTC)


def test_spent_daily_sample_budget_moves_the_deadline_to_the_next_day() -> None:
    now = _kst(2026, 9, 14, 4, 5)
    spent = _sample_attempt("GENERATION_REJECTED", SAMPLE_BODY_DAILY_BUDGET, day=date(2026, 9, 14))

    due = next_recovery_deadline(spent, scheduled_date=date(2026, 9, 14), now=now)

    assert due == _kst(2026, 9, 15, 1, 0).astimezone(UTC)


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

    assert next_recovery_deadline(attempt("PROVIDER_TIMEOUT"), scheduled_date=slot, now=now) is None
    assert next_recovery_deadline(
        attempt("CONTENT_AI_REVIEW_UNAVAILABLE"), scheduled_date=slot, now=now
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


def test_a_slot_on_the_catchup_edge_is_handed_over_instead_of_frozen() -> None:
    """경계선의 슬롯도 소유자가 있다 — `None`을 저장하면 재시도가 영구히 얼어붙는다.

    오늘 스윕은 `[오늘-7, 오늘]`을 보므로 이 슬롯을 집지만, 내일부터는 어떤 창에도
    들지 않는다. 그 사이를 `None`으로 두면 저장된 "집을 스윕이 없다"가 로더 필터와
    `retry_is_due`를 동시에 막아, 내일 22:30 백로그 복구가 날짜를 옮겨도 슬롯이 다시
    살아나지 못한다.
    """

    # 오늘의 마지막 복구 스윕(07:00)이 이 슬롯을 집고 실패한 직후다.
    observed = _kst(2026, 9, 14, 7, 30)
    slot = observed.date() - timedelta(days=RECOVERY_SWEEP_CATCHUP_DAYS)
    attempt = _sample_attempt("GENERATION_REJECTED", 1, day=date(2026, 9, 14))

    assert next_recovery_deadline(attempt, scheduled_date=slot, now=observed) == _kst(
        2026, 9, 14, 23, 30
    ).astimezone(UTC)
    # 창 안쪽(경계선 바로 다음 날)은 종전대로 스윕이 계속 집는다.
    assert next_recovery_deadline(
        attempt, scheduled_date=slot + timedelta(days=1), now=observed
    ) == _kst(2026, 9, 15, 1, 0).astimezone(UTC)


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
    ) == _kst(2026, 9, 17, 1, 0).astimezone(UTC)

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
