"""Retry classes and due times consumed by scheduled content sweeps."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
RECOVERY_SWEEP_HOURS = (1, 4, 7, 23)
ENVIRONMENT_ATTEMPT_BUDGET = 4
# 공개 글 이미지 재인증의 자동 재실행 예산. 지문은 날짜가 아니라 (글, 판)이라
# 같은 판의 반복 실패가 날짜만 바뀌며 무한히 재시도되지 않는다 (H-08).
PUBLISHED_RECERTIFY_ATTEMPT_BUDGET = 3


def published_recertify_key(item_id: uuid.UUID | str, revision: int) -> str:
    """PATCH 디스패치와 복구 sweep이 같은 (글, 판)으로 서로를 중복 제거하는 키."""

    return f"recertify:{item_id}:{revision}"


def published_recertify_sweep_key(
    item_id: uuid.UUID | str, revision: int, attempt: int
) -> str:
    """sweep 재실행 키. 종결된 같은 키를 dispatch가 '이미 실행함'으로 오인하지 않게 한다."""

    return f"{published_recertify_key(item_id, revision)}:s{attempt}"


class GenerationRetryClass(StrEnum):
    INPUT_CHANGE_REQUIRED = "INPUT_CHANGE_REQUIRED"
    ENVIRONMENT_RECOVERABLE = "ENVIRONMENT_RECOVERABLE"
    OPERATOR_REQUIRED = "OPERATOR_REQUIRED"


_ENVIRONMENT_CODES = frozenset(
    {
        "COST_BLOCKED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "CONTENT_AI_REVIEW_UNAVAILABLE",
        "GENERATION_FAILED",
        "IMAGE_GENERATION_FAILED",
        "CONTENT_IMAGE_NOT_READY",
        "CONTENT_IMAGE_NOT_VERIFIED",
    }
)
_INPUT_CODES = frozenset(
    {
        "MISSING_APPROVED_ESSENCE",
        "GENERATION_REJECTED",
        "FORBIDDEN_EXPRESSION",
        "CONTENT_AI_HARD_FINDING",
        "CONTENT_AI_REVIEW_STALE",
    }
)


def retry_class_for(code: str) -> GenerationRetryClass:
    if code in _ENVIRONMENT_CODES:
        return GenerationRetryClass.ENVIRONMENT_RECOVERABLE
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


def retry_is_due(attempt: dict, now: datetime | None = None) -> bool:
    if attempt.get("retry_class") != GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value:
        return False
    legacy_count = (
        0 if attempt.get("reason") == "COST_BLOCKED" else attempt.get("attempt_count")
    )
    try:
        count = int(attempt.get("provider_attempt_count", legacy_count) or 0)
    except (TypeError, ValueError):
        return False
    # A guard deferral makes no provider call, so it must survive a day/month
    # boundary without exhausting the paid-attempt budget.
    if count >= ENVIRONMENT_ATTEMPT_BUDGET:
        return False
    raw_due = attempt.get("next_retry_at")
    if not isinstance(raw_due, str):
        return True
    try:
        due = datetime.fromisoformat(raw_due)
    except ValueError:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) >= due
