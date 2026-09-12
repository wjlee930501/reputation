"""Retry classes and due times consumed by scheduled content sweeps."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
RECOVERY_SWEEP_HOURS = (1, 4, 7, 23)
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
        # 새 KST 일의 첫 스윕은 예산과 무관하게 한 번 더 표본을 뽑는다.
        return True
    try:
        count = int(attempt.get("provider_attempt_count", attempt.get("attempt_count")) or 0)
    except (TypeError, ValueError):
        return False
    if count >= sample_daily_budget(attempt.get("reason")):
        return False
    return _due_time_reached(attempt, observed)


def _due_time_reached(attempt: dict, observed: datetime) -> bool:
    raw_due = attempt.get("next_retry_at")
    if not isinstance(raw_due, str):
        return True
    try:
        due = datetime.fromisoformat(raw_due)
    except ValueError:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return observed >= due


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
    return _due_time_reached(attempt, observed)
