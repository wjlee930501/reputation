"""Retry classes and due times consumed by scheduled content sweeps."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
OVERNIGHT_RECOVERY_HOURS = (1, 4, 7)
DAYTIME_RECOVERY_HOURS = (12, 18, 22)
RECOVERY_SWEEP_HOURS = (*OVERNIGHT_RECOVERY_HOURS, *DAYTIME_RECOVERY_HOURS, 23)
# 23:00 야간 배치가 보는 창은 `[내일, 모레]`, 01·04·07 복구 스윕이 보는 창은
# `[오늘-7일, 모레]`(뒤로는 발행 catch-up과 같은 7일, 앞으로는 야간 배치와 같은
# lookahead)다. 두 창을 모르면 기한이 "다음 스윕 시각"이 되어 그 스윕이 실제로는 집지
# 않는 슬롯까지 재시도 중이라고 말하게 된다.
NIGHTLY_SWEEP_HOUR = 23
NIGHTLY_SWEEP_LOOKAHEAD_DAYS = 2
# `post_publish_review_policy.AUTO_PUBLISH_CATCHUP_DAYS`와 같은 값이어야 한다.
# 이 모듈은 모델을 import하지 않는 잎이라 상수만 복제하고 테스트로 묶어 둔다.
RECOVERY_SWEEP_CATCHUP_DAYS = 7
# 후보 스윕을 찾는 최대 지평. 여기서 못 찾으면 어떤 스윕도 그 슬롯을 집지 않는다.
RECOVERY_DEADLINE_HORIZON_DAYS = 14
# catch-up 창보다 오래된 슬롯은 22:30 백로그 복구(`content_backlog_recovery.reconcile`,
# `core/celery_app.py`의 `stranded-content-recovery`)가 미래 날짜로 옮긴다. 그 실행이
# 끝날 여유 1시간을 더한 시각이 그 슬롯의 다음 판정 시각이다.
BACKLOG_RECOVERY_HOUR = 22
BACKLOG_RECOVERY_MINUTE = 30
BACKLOG_RECOVERY_GRACE = timedelta(hours=1)
ENVIRONMENT_ATTEMPT_BUDGET = 4
DAILY_RESET_ENVIRONMENT_CODES = frozenset({"CONTENT_AI_REVIEW_UNAVAILABLE"})

# 표본(확률적) 실패 예산. LLM은 같은 입력에서도 매번 다른 출력을 낸다 — 한 번의 거절을
# "입력 변경 필요"로 굳히면 배포 전까지 슬롯이 비어 있게 된다. 대신 KST 하루 단위로
# 초기화되는 유한 예산을 주고, 소진된 날이 3일 쌓이면 그때 사람의 일로 올린다.
SAMPLE_BODY_DAILY_BUDGET = 2
SAMPLE_IMAGE_DAILY_BUDGET = 4
SAMPLE_EXHAUSTED_DAY_LIMIT = 3
# 저장된 본문을 작가가 고칠 수 있는 코드(_AUTOMATIC_BODY_REPAIR_CODES)의 하루 세션 예산.
# 재시도 클래스와 분리해 계수한다 — 무조건 시도 기록을 지우면 결정적으로 고칠 수 없는
# 글이 하루 4회 유료 재생성을 영원히 반복한다.
BODY_REPAIR_DAILY_BUDGET = 2
# 저장된 본문을 작가가 스스로 고치는 코드. 이 코드들의 복구를 소유한 것은 재시도 클래스가
# 아니라 수리 세션 예산이므로, 기한·RETRYING·종착 판정이 모두 예산 상태를 본다.
BODY_REPAIR_STATE_KEY = "automatic_body_repair"
BODY_REPAIR_CODES = frozenset(
    {
        "FAQ_FIELDS_MISSING",
        "MISSING_REFERENCES",
        "FORBIDDEN_EXPRESSION",
        "ESSENCE_NOT_ALIGNED",
        "CONTENT_AI_REVIEW_STALE",
    }
)


class GenerationRetryClass(StrEnum):
    INPUT_CHANGE_REQUIRED = "INPUT_CHANGE_REQUIRED"
    ENVIRONMENT_RECOVERABLE = "ENVIRONMENT_RECOVERABLE"
    SAMPLE_RECOVERABLE = "SAMPLE_RECOVERABLE"
    OPERATOR_REQUIRED = "OPERATOR_REQUIRED"


_ENVIRONMENT_CODES = frozenset(
    {
        "COST_BLOCKED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "CONTENT_AI_REVIEW_UNAVAILABLE",
        "GENERATION_FAILED",
    }
)
# 이미지 표본 실패. 하루 4회(야간 스윕 수)까지 재시도하고 다음 KST 일에 초기화된다.
SAMPLE_IMAGE_CODES = frozenset(
    {
        "IMAGE_GENERATION_FAILED",
        "IMAGE_GENERATION_RETRIES_EXHAUSTED",
        "CONTENT_IMAGE_POLICY_REJECTED",
        "CONTENT_IMAGE_NOT_READY",
        "CONTENT_IMAGE_NOT_VERIFIED",
    }
)
# 본문 표본 실패. 작가 세션은 비싸므로 하루 2회로 제한한다.
SAMPLE_BODY_CODES = frozenset({"GENERATION_REJECTED", "CONTENT_AI_HARD_FINDING"})
_SAMPLE_CODES = SAMPLE_IMAGE_CODES | SAMPLE_BODY_CODES
_INPUT_CODES = frozenset(
    {
        "MISSING_APPROVED_ESSENCE",
        "FORBIDDEN_EXPRESSION",
        "CONTENT_AI_REVIEW_STALE",
    }
)


def environment_attempt_period(observed: datetime | None = None) -> str:
    """Return the KST day that owns a bounded environment retry budget."""
    return (observed or datetime.now(UTC)).astimezone(KST).date().isoformat()


def stored_attempt_period(attempt: dict) -> str | None:
    raw_period = attempt.get("attempt_period")
    if isinstance(raw_period, str) and raw_period:
        return raw_period
    raw_observed = attempt.get("observed_at")
    if not isinstance(raw_observed, str):
        return None
    try:
        observed = datetime.fromisoformat(raw_observed)
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return environment_attempt_period(observed)


def has_model_declared_hard_finding(review: object) -> bool:
    """Return whether the stored review itself declared a HARD finding.

    확신도 부족으로 붙는 합성 finding은 UNCERTAIN이다. 모델이 HARD로 단정한 사실·
    의료 안전 지적만 입력(승인 자료) 변경을 요구하는 종착으로 취급한다.
    """

    if not isinstance(review, Mapping):
        return False
    findings = review.get("findings")
    if not isinstance(findings, list):
        return False
    return any(
        isinstance(finding, Mapping)
        and str(finding.get("severity") or "").upper() == "HARD"
        for finding in findings
    )


def sample_daily_budget(reason: object) -> int:
    """Image candidates are cheap and bounded; writer sessions are not."""

    return (
        SAMPLE_IMAGE_DAILY_BUDGET
        if str(reason or "") in SAMPLE_IMAGE_CODES
        else SAMPLE_BODY_DAILY_BUDGET
    )


def retry_class_for(
    code: str, *, model_declared_hard: bool = False
) -> GenerationRetryClass:
    if code in _ENVIRONMENT_CODES:
        return GenerationRetryClass.ENVIRONMENT_RECOVERABLE
    if code == "CONTENT_AI_HARD_FINDING" and model_declared_hard:
        # 모델이 HARD로 단정한 사실·안전 지적은 삭제형 재작성까지 마친 뒤의 종착이다.
        return GenerationRetryClass.INPUT_CHANGE_REQUIRED
    if code in _SAMPLE_CODES:
        return GenerationRetryClass.SAMPLE_RECOVERABLE
    if code in _INPUT_CODES:
        return GenerationRetryClass.INPUT_CHANGE_REQUIRED
    return GenerationRetryClass.OPERATOR_REQUIRED


def next_recovery_sweep(now: datetime | None = None) -> datetime:
    observed = (now or datetime.now(UTC)).astimezone(KST)
    for hour in RECOVERY_SWEEP_HOURS:
        candidate = observed.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate > observed:
            return candidate.astimezone(UTC)
    return (observed + timedelta(days=1)).replace(
        hour=RECOVERY_SWEEP_HOURS[0], minute=0, second=0, microsecond=0
    ).astimezone(UTC)


def _sample_budget_is_spent(attempt: dict, observed: datetime) -> bool:
    """Whether today's KST sample budget for this slot is already spent."""

    if stored_attempt_period(attempt) != environment_attempt_period(observed):
        return False
    try:
        count = int(attempt.get("provider_attempt_count", attempt.get("attempt_count")) or 0)
    except (TypeError, ValueError):
        return True
    return count >= sample_daily_budget(attempt.get("reason"))


def _sweep_window(candidate: datetime) -> tuple[date, date]:
    """Return the scheduled-date window one sweep at ``candidate`` actually claims."""

    day = candidate.date()
    if candidate.hour == NIGHTLY_SWEEP_HOUR:
        return day + timedelta(days=1), day + timedelta(days=NIGHTLY_SWEEP_LOOKAHEAD_DAYS)
    # 복구 스윕의 앞쪽 끝은 야간 배치와 같다. 22:30 백로그 복구가 오래된 슬롯을 **미래**
    # 날짜로 옮기므로, 앞쪽을 `오늘`에서 끊으면 방금 구조한 바로 그 슬롯을 복구 스윕이
    # 다시 집지 못한다.
    return (
        day - timedelta(days=RECOVERY_SWEEP_CATCHUP_DAYS),
        day + timedelta(days=NIGHTLY_SWEEP_LOOKAHEAD_DAYS),
    )


def _candidate_sweeps(observed: datetime):
    """Yield the scheduled sweep datetimes after ``observed``, in time order."""

    for offset in range(RECOVERY_DEADLINE_HORIZON_DAYS + 1):
        day = observed + timedelta(days=offset)
        for hour in sorted(RECOVERY_SWEEP_HOURS):
            candidate = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > observed:
                yield candidate


def _next_sweep_catchup_start(observed: datetime) -> date:
    """The oldest scheduled date any *future* catch-up sweep can still claim."""

    return (
        observed.astimezone(KST).date()
        + timedelta(days=1)
        - timedelta(days=RECOVERY_SWEEP_CATCHUP_DAYS)
    )


def _next_backlog_recovery_deadline(observed: datetime) -> datetime:
    """Return when the 22:30 backlog recovery will have had its turn."""

    run = observed.replace(
        hour=BACKLOG_RECOVERY_HOUR,
        minute=BACKLOG_RECOVERY_MINUTE,
        second=0,
        microsecond=0,
    )
    if run <= observed:
        run += timedelta(days=1)
    return (run + BACKLOG_RECOVERY_GRACE).astimezone(UTC)


def next_repair_session_date(
    repair_state: Mapping | None, now: datetime | None = None
) -> date | None:
    """Return the first KST date that can still buy one automatic repair session.

    오늘 예산이 남았으면 오늘, 오늘 것만 소진됐으면 내일, 소진된 날이 상한만큼 쌓였으면
    `None`(사람의 일)이다.
    """

    observed = (now or datetime.now(UTC)).astimezone(KST)
    today = observed.date()
    if repair_session_is_available(repair_state, observed):
        return today
    try:
        exhausted_days = int((repair_state or {}).get("exhausted_days") or 0)
    except (TypeError, ValueError):
        return None
    if exhausted_days >= SAMPLE_EXHAUSTED_DAY_LIMIT:
        return None
    return today + timedelta(days=1)


def repair_recovery_remains(
    repair_state: Mapping | None, now: datetime | None = None
) -> bool:
    """Whether automatic body repair still owns this blocker (today or later)."""

    return next_repair_session_date(repair_state, now) is not None


def _earliest_eligible_date(
    attempt: dict, observed: datetime, repair_state: Mapping | None = None
) -> date | None:
    """Return the first KST date whose budget can still buy one attempt.

    ``None``은 "남은 예산이 없다"는 뜻이다(종착). 오늘 예산만 소진된 경우에는 내일을
    돌려준다 — 예산 때문에 건너뛴 슬롯의 기한을 스윕마다 미루지 않기 위해, 저장 시점에
    실제로 시도할 수 있는 첫 날을 한 번만 정한다.
    """

    retry_class = attempt.get("retry_class")
    today = observed.astimezone(KST).date()
    if (
        attempt.get("reason") in BODY_REPAIR_CODES
        and retry_class != GenerationRetryClass.INPUT_CHANGE_REQUIRED.value
    ):
        # 이 코드들의 복구는 재시도 클래스가 아니라 수리 세션 예산이 소유한다
        # (`retry_class_for`의 기본값은 다른 용도로 그대로 둔다). 다만 승인된 입력 자체가
        # 틀렸다는 판정(INPUT_CHANGE_REQUIRED)은 작가 세션으로 고칠 수 없는 종착이다.
        return next_repair_session_date(repair_state, observed)
    if retry_class == GenerationRetryClass.SAMPLE_RECOVERABLE.value:
        try:
            exhausted_days = int(attempt.get("exhausted_days") or 0)
        except (TypeError, ValueError):
            return None
        if exhausted_days >= SAMPLE_EXHAUSTED_DAY_LIMIT:
            return None
        if _sample_budget_is_spent(attempt, observed):
            return today + timedelta(days=1)
        return today
    if retry_class != GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value:
        return None
    try:
        count = int(
            attempt.get("provider_attempt_count", attempt.get("attempt_count")) or 0
        )
    except (TypeError, ValueError):
        return None
    if count < ENVIRONMENT_ATTEMPT_BUDGET:
        return today
    if attempt.get("reason") in DAILY_RESET_ENVIRONMENT_CODES:
        # 소진된 예산이 어제 것이면 오늘 이미 초기화됐다. 하루를 더 미루면 새 예산의
        # 하루를 통째로 버린다.
        if stored_attempt_period(attempt) != environment_attempt_period(observed):
            return today
        return today + timedelta(days=1)
    return None


def next_recovery_deadline(
    attempt: dict,
    *,
    scheduled_date: date | None,
    now: datetime | None = None,
    repair_state: Mapping | None = None,
) -> datetime | None:
    """Return when a sweep will actually pick this slot up again, or ``None``.

    스윕마다 창이 다르므로 "다음 스윕 시각"은 기한이 아니다. 후보 시각을 시간순으로
    열거하고, 그 스윕의 창에 이 슬롯의 예정일이 드는 첫 시각을 고른다. 어떤 후보도
    집지 않거나 재시도 예산이 끝났으면 `None`(사람의 일)이다.

    catch-up 창보다 오래된 슬롯은 예외다. 스윕이 아니라 22:30 백로그 복구가 소유하므로
    그 실행 뒤(+1시간)를 기한으로 준다. 복구가 날짜를 옮기면 새 예정일로 시도·기한이
    다시 서고, 옮기지 못하면 그 시각에 사람에게 보이는 것이 옳은 신호다 — 기한 없는
    RETRYING으로 영원히 숨지 않는다.
    """

    if scheduled_date is None:
        return None
    observed = (now or datetime.now(UTC)).astimezone(KST)
    earliest_date = _earliest_eligible_date(attempt, observed, repair_state)
    if earliest_date is None:
        return None
    for candidate in _candidate_sweeps(observed):
        if candidate.date() < earliest_date:
            continue
        window_start, window_end = _sweep_window(candidate)
        if window_start <= scheduled_date <= window_end:
            return candidate.astimezone(UTC)
    # 기준은 오늘의 catch-up 창이 아니라 **다음 스윕이 쓸 창**이다. 오늘 01·04·07이
    # 집었던 창의 첫날(오늘-7)은 내일 창에서 빠지므로, 그날 예정된 슬롯이 마지막 스윕
    # 뒤에 실패하면 어떤 후보 스윕도 다시 담지 못한다. 기준을 오늘 창으로 두면 그 하루가
    # 스윕에도 백로그 복구에도 속하지 않아 `next_retry_at`이 `None`으로 굳고, 표본 실패는
    # 소진 일수가 더 쌓이지 않아 주제 교체로 넘어갈 계단조차 열리지 않는다.
    if scheduled_date < _next_sweep_catchup_start(observed):
        return _next_backlog_recovery_deadline(observed)
    return None


def sample_budget_spent(
    previous: Mapping | None, reason: str, observed: datetime
) -> tuple[int, int]:
    """Return the (daily count, exhausted days) after spending one sample attempt.

    같은 지문 안에서만 누적한다. 날이 바뀌면 하루 예산은 초기화되고, 그날의 예산을
    막 소진한 순간에만 `exhausted_days`가 1 오른다(하루에 두 번 오르지 않는다).
    """

    stored = dict(previous or {})
    period = environment_attempt_period(observed)
    same_period = stored_attempt_period(stored) == period
    try:
        count = int(stored.get("provider_attempt_count") or 0) if same_period else 0
    except (TypeError, ValueError):
        count = 0
    try:
        exhausted_days = int(stored.get("exhausted_days") or 0)
    except (TypeError, ValueError):
        exhausted_days = 0
    count += 1
    if count == sample_daily_budget(reason):
        exhausted_days += 1
    return count, exhausted_days


def repair_session_is_available(
    state: Mapping | None,
    now: datetime | None = None,
    *,
    daily_budget: int = BODY_REPAIR_DAILY_BUDGET,
) -> bool:
    """Return whether one more automatic body-repair session fits today's budget."""

    if not isinstance(state, Mapping):
        return True
    try:
        if int(state.get("exhausted_days") or 0) >= SAMPLE_EXHAUSTED_DAY_LIMIT:
            return False
    except (TypeError, ValueError):
        return False
    if state.get("period") != environment_attempt_period(now):
        return True
    try:
        return int(state.get("count") or 0) < daily_budget
    except (TypeError, ValueError):
        return False


def spend_repair_session(
    state: Mapping | None,
    now: datetime | None = None,
    *,
    daily_budget: int = BODY_REPAIR_DAILY_BUDGET,
) -> dict[str, object]:
    """Persistable counters after one automatic body-repair session is spent."""

    observed = now or datetime.now(UTC)
    period = environment_attempt_period(observed)
    stored = dict(state) if isinstance(state, Mapping) else {}
    try:
        count = int(stored.get("count") or 0) if stored.get("period") == period else 0
    except (TypeError, ValueError):
        count = 0
    try:
        exhausted_days = int(stored.get("exhausted_days") or 0)
    except (TypeError, ValueError):
        exhausted_days = 0
    count += 1
    if count == daily_budget:
        exhausted_days += 1
    first_observed_at = stored.get("first_observed_at")
    return {
        "period": period,
        "count": count,
        "exhausted_days": exhausted_days,
        "first_observed_at": (
            first_observed_at
            if isinstance(first_observed_at, str)
            else observed.isoformat()
        ),
    }


def _sample_retry_is_due(attempt: dict, observed: datetime) -> bool:
    try:
        exhausted_days = int(attempt.get("exhausted_days") or 0)
    except (TypeError, ValueError):
        return False
    if exhausted_days >= SAMPLE_EXHAUSTED_DAY_LIMIT:
        # 3일 소진은 OPERATOR_REQUIRED 전이와 같은 종착이다.
        return False
    if stored_attempt_period(attempt) != environment_attempt_period(observed):
        # 새 KST 일에는 예산이 초기화된다. 다만 저장된 다음 시도 시각은 그대로 지킨다 —
        # 날이 바뀌었다는 이유로 앞당기면 그 시각을 약속한 인시던트 기한과 어긋난다.
        return _due_time_reached(attempt, observed)
    if _sample_budget_is_spent(attempt, observed):
        return False
    return _due_time_reached(attempt, observed)


def _due_time_reached(attempt: dict, observed: datetime) -> bool:
    raw_due = attempt.get("next_retry_at")
    if raw_due is None and "next_retry_at" in attempt:
        # 어떤 스윕도 이 슬롯을 집지 않는다고 판정해 저장한 결정이다. 시각만으로
        # 되살리면 창 밖의 슬롯을 매 스윕이 다시 사게 된다. (키가 아예 없는 레거시
        # 기록은 종전대로 시각 제한 없이 한 번 더 시도한다.)
        return False
    if not isinstance(raw_due, str):
        return True
    try:
        due = datetime.fromisoformat(raw_due)
    except ValueError:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return observed >= due


def _sweep_window_was_abandoned(attempt: Mapping) -> bool:
    """저장된 기록이 "어떤 스윕도 이 슬롯을 다시 집지 않는다"로 굳었는가.

    `next_retry_at`을 명시적으로 `None`으로 저장한 것이 그 결정이다. 키가 아예 없는
    레거시 기록은 결정을 내린 적이 없으므로 여기 해당하지 않는다.
    """

    return "next_retry_at" in attempt and attempt.get("next_retry_at") is None


def retry_is_due(attempt: dict, now: datetime | None = None) -> bool:
    observed = now or datetime.now(UTC)
    retry_class = attempt.get("retry_class")
    if retry_class == GenerationRetryClass.SAMPLE_RECOVERABLE.value:
        return _sample_retry_is_due(attempt, observed)
    if retry_class != GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value:
        return False
    legacy_count = (
        0 if attempt.get("reason") == "COST_BLOCKED" else attempt.get("attempt_count")
    )
    try:
        count = int(attempt.get("provider_attempt_count", legacy_count) or 0)
    except (TypeError, ValueError):
        return False
    budget_reset_due = (
        attempt.get("reason") in DAILY_RESET_ENVIRONMENT_CODES
        and stored_attempt_period(attempt) != environment_attempt_period(observed)
    )
    # Review outages receive a fresh bounded allowance on the next KST day so an
    # already-written due slot cannot remain empty forever. Other provider failures
    # retain their finite H-08 budget.
    if count >= ENVIRONMENT_ATTEMPT_BUDGET and not budget_reset_due:
        return False
    if budget_reset_due and _sweep_window_was_abandoned(attempt):
        # 어제 저장한 "집을 스윕이 없다"는 예측보다, 오늘의 새 예산이 나중에 내려진
        # 결정이다. 게다가 이 판정을 묻는 것은 이미 이 행을 claim한 스윕이므로 창
        # 밖이라는 전제 자체가 틀렸다. 그 예측을 그대로 지키면 하루짜리 검수 장애가
        # 영구 차단으로 굳는다 — 검수를 다시 사지 않으니 저장된 판정도 영원히 그대로다.
        return True
    return _due_time_reached(attempt, observed)


def recovery_is_abandoned(attempt: Mapping | None, now: datetime | None = None) -> bool:
    """예약 복구가 이 기록을 더 집지 않는가. 인시던트가 RETRYING을 말할 자격의 기준."""

    if not isinstance(attempt, Mapping) or not _sweep_window_was_abandoned(attempt):
        return False
    return not retry_is_due(dict(attempt), now)
