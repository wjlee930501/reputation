"""Incident and Slack-outbox projection for content generation failures."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.content import ContentItem
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    NotificationOutbox,
)
from app.services import published_image_recertification as recertification
from app.services.incident_types import (
    IncidentFingerprint,
    IncidentOpenRequest,
    incident_type_of,
)
from app.services.incidents import (
    build_incident_key,
    mark_recovered,
    mark_retrying,
    open_or_touch_incident,
)
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_copy import display_time
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification
from app.services.reference_requirement import references_left_to_operator
from app.workers.generation_retry_policy import (
    BODY_REPAIR_CODES,
    OPERATOR_DECIDES_KEY,
    GenerationRetryClass,
    next_recovery_deadline,
    recovery_is_abandoned,
    repair_recovery_remains,
    retry_class_for,
    retry_is_due,
    stored_attempt_period,
    sweep_claims_slot,
)
from app.workers.generation_retry_policy import (
    BODY_REPAIR_STATE_KEY as BODY_REPAIR_STATE_KEY_POLICY,
)
from app.workers.generation_run_control import (
    GENERATION_REFERENCE_REJECTION_MESSAGE,
    safe_generation_rejection_message,
)
from app.workers.nightly_generation_batch import generation_claim_is_active

logger = logging.getLogger(__name__)

AUTO_REMEDIATION_MAX_GENERATIONS = 2

_MORNING_BODY_NOTIFICATION_CODES = {
    "PROVIDER_TIMEOUT",
    "PROVIDER_UNAVAILABLE",
    "GENERATION_FAILED",
    "CONTENT_NOT_GENERATED",
    "GENERATION_LEASE_ACTIVE",
    "STALE_GENERATION_CLAIM",
}
WEEKLY_REJECTED_GENERATION_CODES: frozenset[str] = frozenset(
    {
        "GENERATION_REJECTED",
        "FAQ_FIELDS_MISSING",
        "MISSING_REFERENCES",
        "FORBIDDEN_EXPRESSION",
        "ESSENCE_NOT_ALIGNED",
        "CONTENT_AI_HARD_FINDING",
        "CONTENT_AI_REVIEW_STALE",
        "CONTENT_IMAGE_POLICY_REJECTED",
    }
)
_MORNING_IMAGE_NOTIFICATION_CODES = {
    "CONTENT_IMAGE_NOT_READY",
    "CONTENT_IMAGE_NOT_VERIFIED",
    "IMAGE_GENERATION_FAILED",
    "IMAGE_GENERATION_RETRIES_EXHAUSTED",
}
# 이미 공개했던 글이 이미지 인증이 풀려 공개 페이지에서 내려간 상태다. 예정 슬롯의
# 아침 마감 게이트와 달리 지금 사람이 결정해야 하므로 첫 open에 한 번 알린다.
PUBLISHED_IMAGE_RECERTIFY_CODES: frozenset[str] = recertification.OPERATOR_REQUIRED_CODES
# The cost guard owns COST_BLOCKED's single hard-stop notification, so generation
# records the incident without opening another outbox row. Published-image
# recertification remains generation-owned and immediate because a public page has
# already regressed and automatic recovery is exhausted.
# 독립 검수 공급자 설정 오류는 자동 재시도 대상이 아니다 — 매일 조용히 실패하는 대신
# 즉시 한 번 알리고 사람이 설정을 고치게 한다.
_IMMEDIATE_GENERATION_NOTIFICATION_CODES: frozenset[str] = (
    frozenset({"COST_BLOCKED", "CONTENT_AI_REVIEW_CONFIG_ERROR"})
    | PUBLISHED_IMAGE_RECERTIFY_CODES
)
_EXTERNALLY_OWNED_IMMEDIATE_NOTIFICATION_CODES = frozenset({"COST_BLOCKED"})
_GENERATION_OWNED_IMMEDIATE_NOTIFICATION_CODES = (
    _IMMEDIATE_GENERATION_NOTIFICATION_CODES
    - _EXTERNALLY_OWNED_IMMEDIATE_NOTIFICATION_CODES
)
_MORNING_GENERATION_NOTIFICATION_CODES = frozenset(
    _MORNING_BODY_NOTIFICATION_CODES
    | _MORNING_IMAGE_NOTIFICATION_CODES
)
_MORNING_DIGEST_ONLY_CODES = frozenset({"MISSING_APPROVED_ESSENCE"})
_KST = ZoneInfo("Asia/Seoul")
_MORNING_NOTIFICATION_START = time(7, 45)

# Codes whose recovery the scheduler already owns (01·04·07·07:45 sweeps and the
# 22:30 stranded-slot recovery). They are worth a Slack line only once they have
# survived the 07:45 prepublish recovery and still block the 08:00 publication.
_PROVIDER_TRANSIENT_NOTIFICATION_CODES = frozenset(
    {
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "GENERATION_LEASE_ACTIVE",
        "STALE_GENERATION_CLAIM",
        "IMAGE_GENERATION_FAILED",
        "CONTENT_IMAGE_NOT_READY",
        "CONTENT_IMAGE_NOT_VERIFIED",
    }
)
_AUTOMATIC_RECOVERY_CODES = frozenset(
    {
        "CONTENT_AI_REVIEW_UNAVAILABLE",
        "IMAGE_GENERATION_FAILED",
        "CONTENT_IMAGE_NOT_READY",
        "CONTENT_IMAGE_NOT_VERIFIED",
    }
)
# 저장된 본문을 작가가 스스로 고치는 코드. 예산이 남아 있는 동안은 자동 복구가 소유한
# 상태이므로 사람의 할 일(OPEN)이 아니라 RETRYING으로 연다.
_AUTOMATIC_BODY_REPAIR_CODES = BODY_REPAIR_CODES
_BODY_REPAIR_STATE_KEY = BODY_REPAIR_STATE_KEY_POLICY
_GENERATION_ATTEMPT_KEY = "generation_attempt"
# One Slack digest per morning batch replaces the per-content-item pages.
PREPUBLISH_MORNING_BATCH = "PREPUBLISH_0745"
PUBLISH_MORNING_BATCH = "PUBLISH_0800"


def _stored_generation_attempt(item) -> dict:
    summary = getattr(item, "essence_check_summary", None)
    attempt = summary.get(_GENERATION_ATTEMPT_KEY) if isinstance(summary, dict) else None
    return dict(attempt) if isinstance(attempt, dict) else {}


def _stored_input_change(attempt: dict, code: str) -> bool:
    """승인된 입력 자체가 틀렸다는 판정인가. 작가 세션으로는 고칠 수 없는 종착이다."""

    return (
        attempt.get("reason") == code
        and attempt.get("retry_class") == GenerationRetryClass.INPUT_CHANGE_REQUIRED.value
    )


def _body_repair_budget_remains(item) -> bool:
    """Whether automatic body repair still owns this blocker (today or on a later day).

    수리 예산이 남아 있는 동안은 시스템의 일이다. 저장된 시도 기록의 기본 분류
    (`OPERATOR_REQUIRED`)보다 이 예산이 앞선다 — 기한도 같은 예산에서 나온다.
    """

    summary = getattr(item, "essence_check_summary", None)
    state = summary.get(_BODY_REPAIR_STATE_KEY) if isinstance(summary, dict) else None
    return repair_recovery_remains(state)


def operator_decides_references(code: str, item) -> bool:
    """진료비·병원 선택 글의 참고자료 보류인가 — 자동 본문 수리가 아니라 사람이 정한다.

    그 주제의 공신력 있는 문서가 본질적으로 없어 작가 세션으로 풀리지 않는다. 수리 예산이
    저장된 분류(`OPERATOR_REQUIRED`)보다 앞서는 다른 본문 수리 코드와 달리 곧바로 종착이다.

    아직 쓰이지 않은 슬롯은 판정할 제목이 행에 없다. 생성이 작가의 제목으로 판정해 남긴 시도
    기록의 표시(`OPERATOR_DECIDES_KEY`)가 그 판정이다(`tasks._run_generation_item`).
    """

    if code != "MISSING_REFERENCES" or item is None:
        return False
    if references_left_to_operator(item):
        return True
    attempt = _stored_generation_attempt(item)
    return (
        not getattr(item, "body", None)
        and attempt.get("reason") == code
        and bool(attempt.get(OPERATOR_DECIDES_KEY))
    )


def scheduled_recovery_owns_blocker(code: str, item) -> bool:
    """Return whether a sweep still owns this cause, so it is not operator work.

    예산이 남아 있는 동안의 자동 복구는 RETRYING이다. 예산이 소진돼
    OPERATOR_REQUIRED로 전이한 뒤에야 원인별 인시던트 1건이 OPEN으로 열린다.
    """

    attempt = _stored_generation_attempt(item)
    if operator_decides_references(code, item):
        return False
    if code in _AUTOMATIC_BODY_REPAIR_CODES and not _stored_input_change(attempt, code):
        # 수리 예산이 저장된 분류보다 앞선다. 예산이 남아 있으면 아직 시스템의 일이다.
        return _body_repair_budget_remains(item)
    if (
        attempt.get("reason") == code
        and attempt.get("retry_class") == GenerationRetryClass.OPERATOR_REQUIRED.value
    ):
        return False
    if attempt.get("reason") == code and recovery_is_abandoned(attempt):
        # 재시도 정책이 이 기록에 "집을 스윕이 없다"고 이미 적었다. 그 상태를 RETRYING
        # 으로 부르면 아무도 소유하지 않은 차단이 기한 없는 "재시도 중"으로 숨는다.
        return False
    if code in _AUTOMATIC_RECOVERY_CODES:
        return True
    return (
        attempt.get("reason") == code
        and attempt.get("retry_class") == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    )


_TERMINAL_RETRY_CLASSES = frozenset(
    {
        GenerationRetryClass.OPERATOR_REQUIRED.value,
        GenerationRetryClass.INPUT_CHANGE_REQUIRED.value,
    }
)


def _stored_retry_deadline(attempt: dict) -> datetime | None:
    raw = attempt.get("next_retry_at")
    if not isinstance(raw, str):
        return None
    try:
        due = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return due if due.tzinfo is not None else due.replace(tzinfo=UTC)


def scheduled_recovery_deadline(item, code: str) -> datetime | None:
    """이 슬롯을 실제로 다시 집는 첫 스윕 시각. 다른 원인의 기한을 빌리지 않는다.

    정본은 워커·게이트가 저장한 시도 기록이다(`_remember_generation_attempt`). 기록이
    이 원인을 가리키지 않는 것은 게이트 호출부가 결정을 먼저 남기지 못했다는 뜻이라,
    같은 규칙으로 한 번 계산해 주되 저장하지는 않는다 — 인시던트 제어는 시도 기록의
    소유자가 아니다.
    """

    if item is None:
        return None
    attempt = _stored_generation_attempt(item)
    if attempt.get("reason") == code:
        return _stored_retry_deadline(attempt)
    logger.warning(
        "generation incident %s opened without a matching stored attempt; "
        "deriving a non-persisted deadline",
        code,
    )
    return next_recovery_deadline(
        {"reason": code, "retry_class": retry_class_for(code).value},
        scheduled_date=getattr(item, "scheduled_date", None),
    )


def environment_recovery_exhausted(code: str, item) -> bool:
    """공급자 장애의 환경 예산이 끝나 예약 복구가 이 빈 슬롯을 더 집지 않는가.

    `ENVIRONMENT_ATTEMPT_BUDGET`을 다 쓰면 재시도 정책이 다음 시도 시각을 `None`으로 저장한다
    (`recovery_is_abandoned`). 그 뒤에도 '다음 예약 배치가 다시 시도합니다'라고 하면 사람이 할
    일이 없는 것처럼 보인다. 본문이 있는 글은 저장 본문 수리가 소유하므로 여기 들지 않는다.
    """

    if code not in _ENVIRONMENT_WAIT_CODES or item is None:
        return False
    if str(getattr(item, "body", None) or "").strip():
        return False
    attempt = _stored_generation_attempt(item)
    return attempt.get("reason") == code and recovery_is_abandoned(attempt)


def operator_retry_releases(item) -> bool:
    """운영센터 “작업 다시 시도”가 이 빈 슬롯의 저장된 억제를 푸는가.

    조치 문구가 “작업 다시 시도”를 권할지 정하는 판정이다. `tasks.regenerate_content_item`이
    Admin이 만든 실행에서 억제를 푸는 조건(#182)과 같은 조건을 여기 따로 적는다 — 두 곳이 갈리지
    않는지는 저장 기록의 분류·원인마다 실제 태스크를 돌려 확인한다
    (`tests/test_generation_incident_copy_retry.py`). 게이트가 남긴 원고 미생성 기록과 환경 실패
    기록(PROVIDER_*·COST_BLOCKED 등 ENVIRONMENT_RECOVERABLE)만 풀린다. 표본 실패(주제 교체 기록
    포함)는 하루 예산이 소유해 기한 전에는 그대로 억제된다.

    이 판정은 **빈 슬롯**만 다룬다. 본문이 있는 글의 독립 검수 공급자 실패
    (CONTENT_AI_REVIEW_UNAVAILABLE)를 재검수만 하려고 푸는 해제는 `regenerate_content_item`의
    별도 경로(재검수 전용 실행)이며 여기에 포함하지 않는다.
    """

    if item is None or str(getattr(item, "body", None) or "").strip():
        return False
    attempt = _stored_generation_attempt(item)
    return (
        attempt.get("reason") == "CONTENT_NOT_GENERATED"
        or attempt.get("retry_class") == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    )


def operator_retry_writes_now(item, now: datetime) -> bool:
    """지금 “작업 다시 시도”를 누르면 이 슬롯의 원고 생성을 다시 시도하는가.

    억제를 푸는 기록(`operator_retry_releases`)이거나, 저장된 기록의 기한이 이미 된 경우다 — 기한이
    된 기록은 누른 실행도 같은 원인 억제에 막히지 않는다(`tasks._generation_attempt_is_unchanged`).
    07:00 스윕이 상한·라운드로빈으로 집지 못한 표본 실패가 07:45 게이트에 그대로 남는 경우가 그것이다.
    """

    if item is None:
        return False
    return operator_retry_releases(item) or retry_is_due(_stored_generation_attempt(item), now)


def announced_recovery_time(item, now: datetime) -> datetime | None:
    """스윕이 소유한 빈 슬롯의 조치 문구가 말할 다음 자동 복구 시각.

    저장된 다음 시도 시각이 이미 지났으면 그 시각은 약속이 아니다 — 그 스윕은 이 슬롯을 집지
    않았다. 그때는 저장 때와 같은 규칙(`next_recovery_deadline`: 스윕마다 다른 창·표본 예산·수리
    예산, `tasks._remember_generation_attempt`와 같은 인자)으로 지금부터 다시 구한다. 다음 정시
    스윕이 아니다 — 23:00 야간 배치는 내일·모레 글만 본다. 구한 시각이 생성 스윕이 이 슬롯을 집는
    시각이 아니면(22:30 백로그 복구의 판정 시각) 원고 생성 시도가 아니므로, 어떤 스윕도 집지 않을
    때와 같이 `None`이다. 그때 문구는 시각을 말하지 않는다(`recovery_time_unannounced`).

    저장된 시각이 아직 오지 않았어도 그 시각에 지금 예정일을 집는 생성 스윕이 없으면(백로그 판정
    시각, 또는 22:30 백로그 복구·일정 변경이 기록을 두고 예정일만 옮긴 경우) 같은 규칙으로 그 시각
    뒤부터 다시 구한다. 기한 전에는 로더가 이 기록을 집지 않으므로(`retry_is_due`) 그 사이의 스윕은
    후보가 아니다.
    """

    if item is None:
        return None
    attempt = _stored_generation_attempt(item)
    retry_at = _stored_retry_deadline(attempt)
    if retry_at is None:
        return None
    scheduled_date = getattr(item, "scheduled_date", None)
    if retry_at <= now or not sweep_claims_slot(retry_at, scheduled_date):
        summary = getattr(item, "essence_check_summary", None)
        retry_at = next_recovery_deadline(
            attempt,
            scheduled_date=scheduled_date,
            now=max(retry_at, now),
            repair_state=(
                summary.get(_BODY_REPAIR_STATE_KEY) if isinstance(summary, dict) else None
            ),
        )
    if retry_at is None or not sweep_claims_slot(retry_at, scheduled_date):
        return None
    return retry_at


def operator_retry_opens_at(item, now: datetime) -> datetime | None:
    """“작업 다시 시도”를 누르면 이 슬롯의 원고 생성을 다시 시도하기 시작하는 첫 시각(``now`` 이후).

    복구 약속이 아니다 — 누른 실행의 같은 원인 억제(`tasks._generation_attempt_is_unchanged`)가
    풀리는 시각이다. 기록이 그대로면 `operator_retry_writes_now`의 값은 저장된 다음 시도 시각과
    기록 날짜(KST 하루 예산)가 바뀌는 자정에서만 바뀐다. 그 경계마다 같은 판정을 물어 처음 참이
    되는 시각을 고른다. 어느 경계에서도 참이 되지 않으면(소진·종착 분류) `None`이다. 생성 지문
    (운영 기준 등)이 바뀌면 그 전에도 풀리지만, 그것은 렌더 뒤의 변화라 여기서 말하지 않는다.
    """

    if item is None:
        return None
    if operator_retry_writes_now(item, now):
        return now
    attempt = _stored_generation_attempt(item)
    boundaries = {_stored_retry_deadline(attempt)}
    period = stored_attempt_period(attempt)
    try:
        day = date.fromisoformat(period) if period else None
    except ValueError:
        day = None
    if day is not None:
        boundaries |= {
            datetime.combine(day + timedelta(days=offset), time(), tzinfo=_KST)
            for offset in (0, 1)
        }
    for moment in sorted(b for b in boundaries if b is not None and b > now):
        if operator_retry_writes_now(item, moment):
            return moment
    return None


def recovery_time_unannounced(item, now: datetime) -> bool:
    """스윕이 다음 시도 시각을 정했던 기록인데 지금 말할 시각이 없는가(시각 없는 조치 문구)."""

    if item is None:
        return False
    return (
        _stored_retry_deadline(_stored_generation_attempt(item)) is not None
        and announced_recovery_time(item, now) is None
    )


def generation_block_is_terminal(code: str, item) -> bool:
    """자동 재시도가 끝난 차단인가. 끝났으면 기한 없는 OPEN(사람의 일)이다."""

    attempt = _stored_generation_attempt(item) if item is not None else {}
    if operator_decides_references(code, item):
        return True
    if code in _AUTOMATIC_BODY_REPAIR_CODES and not _stored_input_change(attempt, code):
        # 수리 예산이 남아 있으면 종착이 아니다 — `scheduled_recovery_owns_blocker`와
        # 같은 술어를 쓴다. 두 판정이 갈리면 기한 없는 OPEN과 RETRYING이 동시에 참이 된다.
        return not _body_repair_budget_remains(item)
    if attempt.get("reason") == code:
        return attempt.get("retry_class") in _TERMINAL_RETRY_CLASSES
    # 모르는 코드의 기본값(OPERATOR_REQUIRED)까지 종착으로 읽으면 lease·stale claim 같은
    # 보고 경로의 기한까지 지운다. 선언된 입력 변경 코드만 기록 없이 종착으로 본다.
    return retry_class_for(code) == GenerationRetryClass.INPUT_CHANGE_REQUIRED


def drop_stored_retry_deadline(item, code: str) -> None:
    """종착으로 굳은 원인의 저장된 다음 시도 시각을 지운다."""

    summary = getattr(item, "essence_check_summary", None)
    attempt = _stored_generation_attempt(item)
    if attempt.get("reason") != code or "next_retry_at" not in attempt:
        return
    attempt.pop("next_retry_at", None)
    updated = dict(summary) if isinstance(summary, dict) else {}
    updated[_GENERATION_ATTEMPT_KEY] = attempt
    item.essence_check_summary = updated


def is_provider_transient_generation_code(code: str) -> bool:
    """Return whether automatic recovery is expected to clear this code by itself."""

    return code in _PROVIDER_TRANSIENT_NOTIFICATION_CODES


def essence_remediation_exhausted(summary) -> bool:
    attempts = (summary or {}).get("automatic_remediation_attempts", 0)
    return (
        isinstance(attempts, int)
        and not isinstance(attempts, bool)
        and attempts >= AUTO_REMEDIATION_MAX_GENERATIONS
    )


def generation_block_digest_due(
    code: str, *, batch: str, remediation_exhausted: bool = False
) -> bool:
    """Return whether one blocked slot belongs in this morning batch's digest."""

    # Every generation code has one Slack owner. Published regressions page
    # immediately; deterministic rejection/gate codes wait for the weekly rollup.
    if code in _IMMEDIATE_GENERATION_NOTIFICATION_CODES | WEEKLY_REJECTED_GENERATION_CODES:
        return False
    # The auto-review task owns snapshot-keyed ESCALATED notifications.
    if code == "MISSING_APPROVED_ESSENCE":
        return False
    if code == "ESSENCE_NOT_ALIGNED" and not remediation_exhausted:
        return False
    if code not in _MORNING_GENERATION_NOTIFICATION_CODES | _MORNING_DIGEST_ONLY_CODES:
        return False
    if batch == PREPUBLISH_MORNING_BATCH and is_provider_transient_generation_code(code):
        return False
    return True


def operator_decides_digest_due(code: str, item, *, batch: str, today: date) -> bool:
    """오늘 발행 예정인 진료비·병원 선택 글의 참고자료 보류를 아침 요약에 한 줄로 올리는가.

    MISSING_REFERENCES는 주간 요약이 소유해 `generation_block_digest_due`가 아침 요약에서
    뺀다. 이 보류는 자동 복구가 풀지 않으므로(`operator_decides_references`) 주간 요약을
    기다리면 운영자가 모르는 채 발행일이 지난다. 그 판정을 넓히지 않고 따로 둔다 — 다른
    코드와 평범한 참고자료 보류의 아침 요약 여부는 그대로다.

    07:45와 08:00 두 요약이 모두 싣는다. 두 요약은 같은 식별자 집합이면 같은 중복 키를
    쓰므로, 두 요약이 함께 싣는 다른 지속 차단처럼 평소 아침에는 08:00이 합쳐져 한 번만
    나간다. 한쪽만 실으면 집합이 갈려 08:00이 다른 줄까지 다시 보낸다. 예정일 당일에만
    싣는다 — 지난 예정일(catch-up)은 매일 반복하지 않는다.
    """

    return (
        batch in (PREPUBLISH_MORNING_BATCH, PUBLISH_MORNING_BATCH)
        and getattr(item, "scheduled_date", None) == today
        and operator_decides_references(code, item)
    )


def generation_safe_cause(code: str) -> str:
    """Operator-safe Korean cause for one generation blocker code."""

    return _generation_safe_cause(code)


def generation_operator_action(code: str, message: str | None = None) -> str:
    """Operator-safe Korean next action for one generation blocker code."""

    return _generation_operator_copy(code, message)[1]


# '지금은 기다리세요'라고 말하는 공급자 장애 코드. 환경 예산이 끝나거나 빈 슬롯이라 “작업 다시
# 시도”가 지금 다시 시도하면 그 말이 틀린다(`ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION`·`PROVIDER_RETRY_NOW_ACTIONS`).
_ENVIRONMENT_WAIT_CODES = frozenset({"PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"})

# 원고 미생성은 07:45·08:00 게이트가 기록한다(`tasks._record_gate_blocker_decision`). 분류는
# OPERATOR_REQUIRED이고 다음 시도 시각이 없어 01·04·07·12·18·22시 복구와 23시 야간 배치 어느
# 것도 이 슬롯을 다시 쓰지 않는다(`_generation_attempt_is_unchanged`). 예외는 생성 지문(운영 기준·
# 유형·측정 질문·검사 규칙 판, `tasks._generation_attempt_context`)이 바뀐 때뿐이다. 운영센터의
# “작업 다시 시도”는 이 기록의 억제를 풀고 원고를 만든다(`tasks.regenerate_content_item`).
CONTENT_NOT_GENERATED_OPERATOR_ACTION = (
    "예약된 자동 복구는 이 글의 원고 생성을 다시 시도하지 않습니다(운영 기준이 새로 승인되는 등 "
    "생성 조건이 바뀔 때만 다시 시도합니다). 운영 센터에서 해당 항목의 “작업 다시 시도”를 누르세요."
)
# 게이트가 원고 미생성을 기록하지 않고 스윕이 소유한 기록을 보존한 슬롯(`tasks.
# _record_gate_blocker_decision`). 표본 실패(주제 교체 뒤의 새 주제 등)는 저장된 다음 시도 시각
# 전에는 “작업 다시 시도”도 같은 기록 때문에 건너뛰어진다. 시각은 `announced_recovery_time`이다.
# 문구는 시도만 말하고 결과(원고)를 약속하지 않는다 — 다시 시도한 생성도 실패할 수 있다.
CONTENT_NOT_GENERATED_SCHEDULED_ACTION = (
    "자동 복구가 {due}에 이 글의 원고 생성을 다시 시도합니다. 그 전에는 “작업 다시 시도”를 눌러도 "
    "원고를 만들지 않으니, 그 뒤 운영 센터에서 결과를 확인하세요."
)
# 같은 보존이지만 “작업 다시 시도”가 지금 다시 시도하는 기록(`operator_retry_writes_now`: 환경 실패
# 기록이거나 기한이 이미 된 기록). 비용 한도 보류는 누른 실행도 비용 가드를 다시 거친다.
CONTENT_NOT_GENERATED_SCHEDULED_RELEASABLE_ACTION = (
    "자동 복구가 {due}에 이 글의 원고 생성을 다시 시도합니다. 원인이 풀렸으면 운영 센터에서 해당 "
    "항목의 “작업 다시 시도”를 눌러 지금 바로 다시 시도할 수 있습니다."
)
# 비해제형인데 누름 게이트가 자동 복구보다 먼저 풀리는 기록(`operator_retry_opens_at` < 자동 복구
# 시각: 22:30 백로그 복구·일정 변경이 예정일을 옮긴 뒤의 저장 시각). '그 전에는'이 자동 복구 시각까지
# 늘어나면 그 사이의 누름에 대해 틀린다. {opens}는 복구 약속이 아니라 누른 실행이 풀리는 시각이다.
CONTENT_NOT_GENERATED_SCHEDULED_EARLY_OPEN_ACTION = (
    "자동 복구가 {due}에 이 글의 원고 생성을 다시 시도합니다. {opens} 전에는 “작업 다시 시도”를 "
    "눌러도 원고를 만들지 않고, 그 뒤에는 원인이 풀렸으면 눌러 다시 시도할 수 있습니다."
)
# 위 문구들의 시각 없는 변형. 스윕이 시각을 정했던 기록이지만 지금부터 이 슬롯을 집는 생성 스윕이
# 없는 경우다(`recovery_time_unannounced`: 예산 소진, catch-up 창을 벗어나 22:30 백로그 복구가 날짜를
# 옮길 차례인 글). 그때는 자동 복구가 다시 시도한다고도, 하지 않는다고도 말하지 않는다 — 백로그
# 복구가 날짜를 옮기면 다시 시도할 수 있다. '눌러도 원고를 만들지 않는다'는 누름 게이트가 풀리는
# 시각({opens}, `operator_retry_opens_at`)까지만 말한다 — 그 글은 다시 그려지지 않을 수 있어 시한
# 없는 '지금은'은 게이트가 풀린 뒤 거짓으로 남는다(#187 3차 차단). 게이트가 풀리지 않는 기록은
# 누름에 대해 아무것도 단정하지 않는다(`CONTENT_NOT_GENERATED_UNSCHEDULED_CLOSED_ACTION`).
CONTENT_NOT_GENERATED_UNSCHEDULED_ACTION = (
    "예약된 자동 복구가 이 글의 원고 생성을 다시 시도할 시각이 정해져 있지 않습니다. {opens} 전에는 "
    "“작업 다시 시도”를 눌러도 원고를 만들지 않고, 그 뒤에는 원인이 풀렸으면 눌러 다시 시도할 수 "
    "있습니다."
)
CONTENT_NOT_GENERATED_UNSCHEDULED_CLOSED_ACTION = (
    "예약된 자동 복구가 이 글의 원고 생성을 다시 시도할 시각이 정해져 있지 않습니다. 운영 센터에서 "
    "이 글의 상태를 확인하세요."
)
CONTENT_NOT_GENERATED_UNSCHEDULED_RELEASABLE_ACTION = (
    "예약된 자동 복구가 이 글의 원고 생성을 다시 시도할 시각이 정해져 있지 않습니다. 원인이 "
    "풀렸으면 운영 센터에서 해당 항목의 “작업 다시 시도”를 눌러 지금 바로 다시 시도할 수 있습니다."
)
# 환경 예산을 다 쓴 공급자 장애(`environment_recovery_exhausted`). 예약 복구는 더 집지 않지만
# “작업 다시 시도”는 환경 실패 기록의 억제를 풀어 바로 원고를 만든다(`operator_retry_releases`).
ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION = (
    "자동 재시도 횟수를 모두 사용해 예약된 자동 복구가 이 글의 원고를 더 만들지 않습니다. "
    "외부 서비스가 정상인지 확인한 뒤 운영 센터에서 해당 항목의 “작업 다시 시도”를 누르세요."
)
# 예산이 남은 공급자 장애의 빈 슬롯. 다음 배치가 다시 시도하고, 환경 실패 기록은 “작업 다시
# 시도”가 억제를 풀어 바로 다시 시도한다(`operator_retry_releases`) — 아침 요약의 '서비스가
# 복구됐으면 “작업 다시 시도”'(`notification_copy.blocker_copy`)와 같은 말이다. 본문이 있는 글은
# 누른 실행이 이 기록을 풀지 않으므로 종전 '지금은 기다리세요'를 쓴다.
_PROVIDER_RETRY_NOW = (
    "다음 예약 배치가 자동으로 다시 시도합니다. 서비스가 복구됐으면 운영 센터에서 해당 항목의 "
    "“작업 다시 시도”를 눌러 지금 바로 다시 시도할 수 있습니다."
)
PROVIDER_RETRY_NOW_ACTIONS = {
    "PROVIDER_TIMEOUT": f"일시적인 응답 지연입니다. {_PROVIDER_RETRY_NOW}",
    "PROVIDER_UNAVAILABLE": f"일시적인 외부 서비스 장애입니다. {_PROVIDER_RETRY_NOW}",
}


# 생성 거절 중 원인이 참고자료 확보 실패인 경우의 조치. 예전에는 GENERATION_REJECTED 전체가
# "가격·지역·검색 구조 게이트" 문구를 받아 운영자가 엉뚱한 곳을 봤다(2026-09-29 점검 §4).
# 사람이 이 문구를 보는 행은 대개 본문이 없다(표본 예산·주제 교체를 다 쓴 빈 슬롯). 저장 본문
# 수리가 실패한 행은 옛 본문이 남아 있다. 두 경우 모두 "콘텐츠 수정"은 제목·본문·참고 자료
# 전체 편집을 열고, 저장하면 본문이 있는 행이라 일반 스윕·발행 전 검사로 돌아간다(저장된 거절
# 기록은 본문 없는 행만 막는다). 참고 자료 없이 저장한 필수 유형 글은 복구 스윕이 작가에게
# 돌려 본문을 다시 쓴다(`nightly_generation_batch._needs_generation_recovery`) — 그래서 문서를
# 함께 넣으라고 하고, 빼면 본문이 다시 쓰일 수 있다고 말한다. "다음 자동 재시도"는 약속하지
# 않는다. 조작은 콘텐츠 화면의 실제 버튼뿐이다(`tests/test_reference_operator_copy.py`).
REFERENCE_REJECTION_OPERATOR_ACTION = (
    "실제 문서 확인을 통과한 공신력 있는 참고 자료가 없어 원고를 저장하지 못했습니다. "
    "콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 눌러 제목·본문을 질환·검사 안내 글로 쓰거나 "
    "고치고, 글의 주장을 직접 뒷받침하는 공공·학술 기관 문서를 “참고 자료 추가”로 넣어 "
    "저장하세요. 저장한 글은 일반 글과 같이 발행 전 검사를 거칩니다. 참고 자료 없이 저장하면 "
    "자동 복구가 참고 자료를 찾으며 본문을 다시 쓸 수 있습니다. 병원 누리집은 참고 자료가 될 수 "
    "없고, 참고 자료 없이는 발행되지 않습니다."
)

# 진료비·병원 선택 글의 참고자료 보류(`operator_decides_references`). 같은 MISSING_REFERENCES지만
# 자동 복구가 본문을 다시 쓰지 않으므로 "다음 자동 복구가 다시 씁니다"라고 말하지 않는다.
REFERENCES_OPERATOR_DECIDES_CAUSE = (
    "진료비·병원 선택처럼 공신력 있는 문서가 없는 주제라 참고 자료를 자동으로 채우지 않고 "
    "발행을 보류했습니다."
)
# Admin에 실제로 있는 조작만 말한다 — 콘텐츠 화면의 "콘텐츠 수정"(참고 자료 추가·제목·본문
# 편집)뿐이고, 항목 종료·재생성·발행일 이동 버튼은 콘텐츠 화면에 없다
# (`admin/lib/content-page-contract.test.ts`, `tests/test_reference_operator_copy.py`).
# 검증된 문서 목록의 문서는 관리자 수정이 422로 거절하고(`disallowed_curated_references`),
# 병원 누리집은 허용 출처가 아니며, 참고 자료가 필수인 글은 0개로 발행되지 않는다.
# 질환·검사 안내 글로 고치면 더 이상 진료비·병원 선택 글이 아니라(제목 기준) 평범한 참고자료
# 보류가 되고, 문서 없이 저장한 본문은 저장 본문 수리(`BODY_REPAIR_CODES`)가 작가에게 돌려 다시
# 쓴다. 진료비·병원 선택 글로 두는 동안에도 새 운영 기준 승인은 옛 기준의 본문을 한 번 다시
# 쓴다(`tasks._generate_single_content_item`) — 그래서 '다시 쓰지 않습니다'에 단서를 붙인다.
REFERENCES_OPERATOR_DECIDES_ACTION = (
    "콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 눌러 둘 중 하나를 하세요. 글의 주장을 직접 "
    "뒷받침하는 공공·학술 기관 문서가 있으면 “참고 자료 추가”로 넣고 저장합니다. 없으면 "
    "제목·본문을 질환·검사 안내 글로 고쳐 저장합니다 — 다음 발행 확인이 검증된 문서로 참고 "
    "자료를 채울 수 있습니다. 병원 누리집과 검증된 문서 목록의 질환 문서는 이 글의 참고 자료가 "
    "될 수 없고, 참고 자료 없이는 발행되지 않습니다. 질환·검사 안내 글로 고쳐 참고 자료 없이 "
    "저장하면 자동 복구가 참고 자료를 찾으며 본문을 다시 쓸 수 있습니다. 진료비·병원 선택 글로 "
    "두면 자동 복구는 운영 기준이 새로 승인되는 등 생성 조건이 바뀌기 전에는 이 글을 다시 쓰지 "
    "않습니다."
)


# 원고가 없는 진료비·병원 선택 슬롯의 사람 결정 보류. 고칠 제목·본문이 없으므로 '새로 쓰기'를
# 말한다. 이 기록(MISSING_REFERENCES·OPERATOR_REQUIRED)은 “작업 다시 시도”의 해제 조건 밖이라
# 누른 실행도 작가를 부르지 않는다(`operator_retry_releases`).
REFERENCES_OPERATOR_DECIDES_UNWRITTEN_ACTION = (
    "아직 원고가 없는 글입니다. 운영 센터의 “작업 다시 시도”를 눌러도 이 글은 쓰이지 않습니다. 콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 눌러 제목·본문을 새로 써서 저장해 주세요. 진료비·병원 선택 글로 쓰려면 주장을 직접 뒷받침하는 공공·학술 기관 문서를 “참고 자료 추가”로 함께 넣어야 하며, 병원 누리집과 검증된 문서 목록의 문서는 저장이 거절됩니다. 질환·검사 안내 글로 쓰면 참고 자료 없이 저장해도 자동 복구가 참고 자료를 찾습니다. 그대로 두면 생성 조건이 바뀌기 전에는 자동 복구가 이 글을 쓰지 않고, 참고 자료 없이는 발행되지 않습니다."
)
# 원고가 없는 글의 독립 검수 HARD 지적. 고칠 본문이 없으므로 직접 쓰기를 말한다. 저장한 글은
# 독립 검수를 다시 받는다(`content_publication`).
CONTENT_AI_HARD_FINDING_UNWRITTEN_ACTION = (
    "아직 원고가 없는 글입니다. “작업 다시 시도”나 승인 자료 추가만으로는 다시 쓰이지 않고, 운영 기준이 새로 승인되는 등 생성 조건이 바뀔 때 자동 복구가 다시 시도합니다. 지금 준비하려면 “콘텐츠 수정”으로 제목·본문을 직접 쓰고 근거 문서를 “참고 자료 추가”로 넣어 저장하세요. 저장한 글은 독립 검수를 다시 받습니다."
)


def _has_body(item) -> bool:
    return bool(str(getattr(item, "body", None) or "").strip())


def _generation_operator_copy(
    code: str,
    message: str | None = None,
    *,
    retry_at: datetime | None = None,
    retry_unscheduled: bool = False,
    retry_releasable: bool = False,
    retry_opens_at: datetime | None = None,
    environment_exhausted: bool = False,
    unwritten: bool = False,
    recovery_owned: bool = False,
) -> tuple[str, str]:
    """`retry_at`은 스윕이 아직 소유한 원고 미생성 슬롯의 다음 자동 복구 시각
    (`announced_recovery_time`), `retry_unscheduled`는 스윕이 시각을 정했던 기록인데 지금 말할 시각이
    없다는 판정(`recovery_time_unannounced`), `retry_releasable`은 지금 “작업 다시 시도”를 누르면 원고
    생성을 다시 시도하는지(`operator_retry_writes_now`), `retry_opens_at`은 누르면 다시 시도하기 시작하는
    첫 시각(`operator_retry_opens_at`, 복구 약속이 아니다), `environment_exhausted`는 공급자 장애의 환경
    예산이 끝났다는 판정, `unwritten`은 글에 본문이 아직 없다는 사실, `recovery_owned`는 예약 복구가
    이 원인을 아직 소유한다는 판정(`scheduled_recovery_owns_blocker`)이다."""

    impact = (
        "이미 공개한 글이 대표 이미지 인증이 풀려 공개 페이지에서 내려가 있습니다."
        if code in PUBLISHED_IMAGE_RECERTIFY_CODES
        else "발행 예정 콘텐츠가 저장되지 않아 병원 채널에 제때 공개되지 않습니다."
    )
    actions = {
        "PROVIDER_TIMEOUT": (
            "일시적인 응답 지연입니다. 다음 예약 배치가 자동으로 다시 시도하므로 지금은 기다리세요."
        ),
        "PROVIDER_UNAVAILABLE": (
            "일시적인 외부 서비스 장애입니다. 다음 예약 배치가 자동으로 다시 시도하므로 지금은 기다리세요."
        ),
        "GENERATION_REJECTED": (
            "가격·지역·검색 구조 자동 검수 게이트가 재작성 후에도 통과되지 않았습니다. "
            "운영 센터에서 게이트 문구와 승인된 병원 정보를 확인하세요."
        ),
        "MISSING_APPROVED_ESSENCE": (
            "시스템이 새 자료 처리와 최신 전체 자료 기반 운영 기준 검토·승인을 자동으로 "
            "이어갑니다. 발행 지연이 계속되는 병원만 운영 센터에서 원자료 수집 오류를 "
            "확인하세요."
        ),
        "COST_BLOCKED": (
            "운영 센터 하단의 “비용·자동 작업 안전장치”를 펼치세요. 전체 중지 상태면 “중지 해제”를 "
            "누르고, 오늘 한도에 도달했다면 계정 소유자에게 “오늘 한도 2배” 조치를 요청하세요."
        ),
        "GENERATION_LEASE_ACTIVE": (
            "다른 생성 작업이 진행 중입니다. 완료될 때까지 기다린 뒤 운영 센터를 새로고침하세요."
        ),
        "STALE_GENERATION_CLAIM": (
            "이전 작업 기록이 남아 자동 생성이 시작되지 않았습니다. 운영 센터를 새로고침해 "
            "현재 상태를 다시 확인하세요."
        ),
        "CONTENT_NOT_GENERATED": CONTENT_NOT_GENERATED_OPERATOR_ACTION,
        "MISSING_REFERENCES": (
            "참고 자료가 실제 문서 확인(없는 문서·빈 페이지·주제 불일치)에서 모두 빠지고 "
            "검증된 목록에서도 채우지 못해 발행을 보류했습니다. 다음 자동 복구가 검증된 "
            "문서로 본문 다시 쓰기를 시도합니다. 반복되면 병원 정보 탭에서 이 글의 주제를 확인하세요."
        ),
        "FORBIDDEN_EXPRESSION": (
            "운영 센터에서 의료광고 금지 표현이 차단된 공개 필드와 승인된 대체 문구를 확인하세요."
        ),
        "ESSENCE_NOT_ALIGNED": ("병원 온보딩에서 승인된 운영 기준과 차단된 문구를 확인하세요."),
        "FAQ_FIELDS_MISSING": ("운영 센터에서 FAQ 질문과 직접 답변 요약의 누락 원인을 확인하세요."),
        # 항목 종료 버튼은 없다. 콘텐츠 화면의 편집(“콘텐츠 수정”·“참고 자료 추가”)으로 고친
        # 후보는 독립 검수가 다시 본다(`content_publication`: 차단 뒤 바뀐 후보는 STALE → 재검수).
        "CONTENT_AI_HARD_FINDING": (
            "지적된 사실을 병원 정보 탭의 승인 자료에 채우세요. 승인 자료가 바뀌면 다음 "
            "자동 복구가 그 자료로 본문 다시 쓰기를 시도합니다. 지적이 사실과 다르면 콘텐츠 탭에서 "
            "이 글의 “콘텐츠 수정”을 눌러 지적된 내용을 고치거나, 그 내용을 직접 뒷받침하는 "
            "공공·학술 기관 문서를 “참고 자료 추가”로 넣어 저장하세요. 저장한 글은 독립 검수를 "
            "다시 받습니다."
        ),
        "CONTENT_AI_REVIEW_STALE": ("운영 센터에서 변경된 원고와 재검수 대기 상태를 확인하세요."),
        "CONTENT_AI_REVIEW_UNAVAILABLE": (
            "시스템 재시도 중입니다. 다음 예약 배치가 독립 검수를 다시 실행합니다."
        ),
        "CONTENT_AI_REVIEW_CONFIG_ERROR": (
            "독립 검수 공급자 인증과 모델 설정을 확인하세요. 자동 재시도 대상이 아닙니다."
        ),
        "CONTENT_IMAGE_NOT_READY": (
            "시스템 재시도 중입니다. 다음 예약 배치가 대표 이미지 생성을 다시 시도합니다."
        ),
        "CONTENT_IMAGE_NOT_VERIFIED": (
            "시스템 재시도 중입니다. 다음 예약 배치가 대표 이미지 정책 검사를 다시 실행합니다."
        ),
        "IMAGE_GENERATION_FAILED": (
            "시스템 재시도 중입니다. 본문은 보존되며 다음 예약 배치가 대체 이미지 경로를 다시 실행합니다."
        ),
        "IMAGE_GENERATION_RETRIES_EXHAUSTED": (
            "대표 이미지 자동 재시도 예산을 모두 사용했습니다. 운영 센터에서 공급자 상태를 확인하세요."
        ),
        "CONTENT_IMAGE_POLICY_REJECTED": (
            "대표 이미지 후보가 정책 검사에서 거절되었습니다. 주제 또는 승인된 이미지 방향을 확인하세요."
        ),
        # 공개 글에는 관리자 이미지 업로드 경로가 없고 "대표 이미지 다시 생성"은 PUBLISHED를
        # 거절한다. 실제로 사람이 할 수 있는 조치만 적는다.
        recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED: recertification.OPERATOR_ACTION,
        recertification.PUBLISHED_IMAGE_MISSING: recertification.OPERATOR_ACTION,
        recertification.PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED: (
            f"자동 재인증이 반복 실패했습니다. {recertification.OPERATOR_ACTION}"
        ),
    }
    if code == "GENERATION_REJECTED" and message == GENERATION_REFERENCE_REJECTION_MESSAGE:
        return impact, REFERENCE_REJECTION_OPERATOR_ACTION
    if code == "MISSING_REFERENCES" and message == REFERENCES_OPERATOR_DECIDES_CAUSE:
        return impact, (
            REFERENCES_OPERATOR_DECIDES_UNWRITTEN_ACTION
            if unwritten
            else REFERENCES_OPERATOR_DECIDES_ACTION
        )
    if code == "CONTENT_AI_HARD_FINDING" and unwritten and not recovery_owned:
        # '다시 쓰이지 않고'는 예약 복구가 소유하지 않을 때만 참이다(종착·비소유). 표본 예산이 남은
        # 기록이면 종전 조치를 그대로 쓴다.
        return impact, CONTENT_AI_HARD_FINDING_UNWRITTEN_ACTION
    if code == "CONTENT_NOT_GENERATED" and retry_at is not None:
        if retry_releasable:
            template = CONTENT_NOT_GENERATED_SCHEDULED_RELEASABLE_ACTION
        elif retry_opens_at is not None and retry_opens_at < retry_at:
            template = CONTENT_NOT_GENERATED_SCHEDULED_EARLY_OPEN_ACTION
        else:
            template = CONTENT_NOT_GENERATED_SCHEDULED_ACTION
        return impact, template.format(
            due=display_time(retry_at),
            opens=display_time(retry_opens_at) if retry_opens_at is not None else "",
        )
    if code == "CONTENT_NOT_GENERATED" and retry_unscheduled:
        if retry_releasable:
            return impact, CONTENT_NOT_GENERATED_UNSCHEDULED_RELEASABLE_ACTION
        if retry_opens_at is None:
            return impact, CONTENT_NOT_GENERATED_UNSCHEDULED_CLOSED_ACTION
        return impact, CONTENT_NOT_GENERATED_UNSCHEDULED_ACTION.format(
            opens=display_time(retry_opens_at)
        )
    if code in _ENVIRONMENT_WAIT_CODES and environment_exhausted:
        return impact, ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION
    if code in _ENVIRONMENT_WAIT_CODES and unwritten and retry_releasable:
        return impact, PROVIDER_RETRY_NOW_ACTIONS[code]
    action = actions.get(
        code,
        "운영 센터에 “작업 다시 시도”가 보이면 누르고 완료 결과를 확인하세요.",
    )
    return impact, action


def _generation_safe_cause(code: str) -> str:
    return {
        "PROVIDER_TIMEOUT": "콘텐츠 생성 서비스의 응답이 제시간에 오지 않았습니다.",
        "PROVIDER_UNAVAILABLE": "콘텐츠 생성 서비스를 일시적으로 사용할 수 없습니다.",
        "GENERATION_REJECTED": (
            "가격·지역·검색 구조 자동 검수 게이트가 재작성 후에도 통과되지 않았습니다."
        ),
        "MISSING_APPROVED_ESSENCE": "승인된 콘텐츠 운영 기준이 없어 자동 생성을 시작하지 않았습니다.",
        "COST_BLOCKED": "오늘 설정된 사용 한도에 도달해 자동 생성을 시작하지 않았습니다.",
        "IMAGE_GENERATION_FAILED": "본문은 준비됐지만 대표 이미지를 만들지 못했습니다.",
        "GENERATION_LEASE_ACTIVE": "같은 콘텐츠의 다른 생성 작업이 아직 진행 중입니다.",
        "STALE_GENERATION_CLAIM": "완료되지 않은 이전 작업 기록 때문에 새 생성을 시작하지 못했습니다.",
        "CONTENT_NOT_GENERATED": "발행 시각까지 콘텐츠 제목과 본문이 준비되지 않았습니다.",
        # 실패가 아니라 자동 폴백의 중간 상태다 — 같은 슬롯을 다른 주제로 다시 쓴다.
        "TOPIC_SWAPPED": "같은 주제로 자동 생성이 소진되어 다른 주제로 원고 생성을 다시 시도합니다.",
        "MISSING_REFERENCES": (
            "실제 문서 확인을 통과한 공신력 있는 참고 자료를 확보하지 못해 발행을 보류했습니다."
        ),
        "FAQ_FIELDS_MISSING": "FAQ 질문과 직접 답변 요약이 준비되지 않았습니다.",
        "FORBIDDEN_EXPRESSION": "의료광고 금지 표현이 발견되어 공개를 중단했습니다.",
        "ESSENCE_NOT_ALIGNED": "콘텐츠가 승인된 운영 기준의 자동 검사를 통과하지 못했습니다.",
        "CONTENT_AI_HARD_FINDING": "독립 검수의 사실·의료 안전 지적이 해결되지 않았습니다.",
        "CONTENT_AI_REVIEW_STALE": "원고 변경 뒤 독립 재검수가 아직 완료되지 않았습니다.",
        "CONTENT_AI_REVIEW_UNAVAILABLE": "독립 AI 검수 공급자를 일시적으로 사용할 수 없습니다.",
        "CONTENT_AI_REVIEW_CONFIG_ERROR": "독립 AI 검수 공급자 설정이 완료되지 않았습니다.",
        "CONTENT_IMAGE_NOT_READY": "대표 이미지가 준비되지 않아 공개를 중단했습니다.",
        "CONTENT_IMAGE_NOT_VERIFIED": "대표 이미지의 자동 정책 검사가 완료되지 않아 공개를 중단했습니다.",
        "IMAGE_GENERATION_RETRIES_EXHAUSTED": "대표 이미지 자동 재시도 예산을 모두 사용했습니다.",
        "CONTENT_IMAGE_POLICY_REJECTED": "대표 이미지 후보가 자동 정책 검사에서 거절되었습니다.",
        **recertification.SAFE_MESSAGES,
    }.get(code, "자동 콘텐츠 생성 작업이 완료되지 않았습니다.")


def _generation_severity(code: str) -> IncidentSeverity:
    """Separate expected preparation/wait states from publication blockers."""

    if code in {
        "MISSING_APPROVED_ESSENCE",
        "COST_BLOCKED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "GENERATION_LEASE_ACTIVE",
        "STALE_GENERATION_CLAIM",
    }:
        return IncidentSeverity.MEDIUM
    return IncidentSeverity.HIGH


def _fingerprint(code: str) -> IncidentFingerprint:
    return {
        "PROVIDER_TIMEOUT": IncidentFingerprint.PROVIDER_TIMEOUT,
        "PROVIDER_UNAVAILABLE": IncidentFingerprint.PROVIDER_REJECTED,
        "GENERATION_REJECTED": IncidentFingerprint.PROVIDER_REJECTED,
        "MISSING_APPROVED_ESSENCE": IncidentFingerprint.MISSING_PREREQUISITE,
        "COST_BLOCKED": IncidentFingerprint.COST_BLOCKED,
        "IMAGE_GENERATION_FAILED": IncidentFingerprint.RENDER_FAILED,
        "IMAGE_GENERATION_RETRIES_EXHAUSTED": IncidentFingerprint.RENDER_FAILED,
        "GENERATION_LEASE_ACTIVE": IncidentFingerprint.VALIDATION_FAILED,
        "STALE_GENERATION_CLAIM": IncidentFingerprint.VALIDATION_FAILED,
        "CONTENT_NOT_GENERATED": IncidentFingerprint.MISSING_PREREQUISITE,
        "MISSING_REFERENCES": IncidentFingerprint.MISSING_PREREQUISITE,
        "FORBIDDEN_EXPRESSION": IncidentFingerprint.SAFETY_BLOCKED,
        "ESSENCE_NOT_ALIGNED": IncidentFingerprint.VALIDATION_FAILED,
        "CONTENT_IMAGE_NOT_READY": IncidentFingerprint.RENDER_FAILED,
        "CONTENT_IMAGE_NOT_VERIFIED": IncidentFingerprint.RENDER_FAILED,
        "CONTENT_IMAGE_POLICY_REJECTED": IncidentFingerprint.SAFETY_BLOCKED,
        # 재인증 차단 세 코드는 지문을 공유한다. 예산 소진으로 열린 건 위에 같은 판의
        # 거절이 겹쳐도 새 incident·새 Slack이 아니라 그 한 건이 갱신된다.
        **dict.fromkeys(
            recertification.OPERATOR_REQUIRED_CODES,
            recertification.INCIDENT_FINGERPRINT,
        ),
    }.get(code, IncidentFingerprint.UNKNOWN)


def content_generation_object_id(item_id: uuid.UUID, topic_swap_count: int = 0) -> str:
    """주제 교체마다 새 epoch를 연다.

    교체 뒤의 같은 코드 실패는 **새 인시던트**를 열어야 한다 — 옛 주제의 episode를
    다시 열거나, 옛 주제에 붙은 ACK 단축 경로를 새 주제에 적용하면 안 된다.
    `source_id`는 종전대로 글 id라 큐 조인·성공 시 자동 종료는 그대로다.
    """

    if topic_swap_count > 0:
        return f"{item_id}#t{topic_swap_count}"
    return str(item_id)


def generation_incident_dedupe_key(
    item_id: uuid.UUID, code: str, *, topic_swap_count: int = 0
) -> str:
    """한 글·한 원인·한 epoch의 생성 인시던트 중복 제거 키."""

    return build_incident_key(
        "content_generation",
        "content_item",
        content_generation_object_id(item_id, topic_swap_count),
        _fingerprint(code),
    )


def _incident_identity(
    code: str,
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    subject_hash: str | None = None,
    topic_swap_count: int = 0,
) -> tuple[str, str, str]:
    """Use one durable incident per hospital for a hospital-level preparation gate."""

    if code == "MISSING_APPROVED_ESSENCE":
        # 옛 `/essence` 경로는 현황 탭의 예외 카드로 합쳐졌다(route-redirects.ts).
        return "hospital", str(hospital_id), f"/hospitals/{hospital_id}"
    if code in PUBLISHED_IMAGE_RECERTIFY_CODES and subject_hash is not None:
        # 사람이 내리는 결정은 이미지 subject(유형 + 제목)마다 다르다. 같은 subject의
        # 반복 디스패치는 한 건으로 묶고, 다음 제목 편집은 새 건으로 연다. 판으로 묶으면
        # 제목과 무관한 편집·Essence 재승인이 같은 거절로 두 번째 건을 연다.
        return (
            recertification.INCIDENT_OBJECT_TYPE,
            recertification.incident_object_id(item_id, subject_hash),
            "/operations",
        )
    return (
        "content_item",
        content_generation_object_id(item_id, topic_swap_count),
        "/operations",
    )


def generation_notify_requested(code: str) -> bool:
    """Return whether generation itself owns an immediate or morning notification."""

    return code in (
        _GENERATION_OWNED_IMMEDIATE_NOTIFICATION_CODES
        | _MORNING_GENERATION_NOTIFICATION_CODES
    )


def generation_notification_cadence(code: str) -> str:
    """Expose the mutually exclusive Slack owner used by workers and tests."""

    if code in _IMMEDIATE_GENERATION_NOTIFICATION_CODES:
        return "IMMEDIATE"
    if code in WEEKLY_REJECTED_GENERATION_CODES:
        return "WEEKLY"
    if code in _MORNING_GENERATION_NOTIFICATION_CODES | _MORNING_DIGEST_ONLY_CODES:
        return "MORNING"
    return "NONE"


def _morning_notification_due(
    *, code: str, item: ContentItem | None, observed_at: datetime
) -> bool:
    """Page only for an unresolved due slot at/after its 07:45 KST close sweep."""

    if code in _IMMEDIATE_GENERATION_NOTIFICATION_CODES:
        return True
    if code not in _MORNING_GENERATION_NOTIFICATION_CODES or item is None:
        return False
    if code == "ESSENCE_NOT_ALIGNED" and not essence_remediation_exhausted(
        getattr(item, "essence_check_summary", None)
    ):
        return False
    local_now = observed_at.astimezone(_KST)
    scheduled_date = getattr(item, "scheduled_date", None)
    if scheduled_date is None or scheduled_date > local_now.date():
        return False
    if local_now.time().replace(tzinfo=None) < _MORNING_NOTIFICATION_START:
        return False

    body_present = bool(str(getattr(item, "body", "") or "").strip())
    image_present = bool(str(getattr(item, "image_url", "") or "").strip())
    if code in _MORNING_BODY_NOTIFICATION_CODES:
        return not body_present
    if code == "CONTENT_IMAGE_NOT_VERIFIED":
        return (
            body_present
            and image_present
            and not bool(getattr(item, "image_policy_verified_at", None))
        )
    if code in _MORNING_IMAGE_NOTIFICATION_CODES:
        return body_present and not image_present
    return body_present


def _should_send_generation_notification(
    *,
    notify_requested: bool,
    previous_state: str | None,
    code: str | None = None,
    has_open_cause: bool = False,
    notification_already_enqueued: bool = False,
    first_seen_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """Page once per episode after the caller proves the morning gate is still open."""

    del code, first_seen_at, last_seen_at, now
    if previous_state == IncidentState.ACKNOWLEDGED.value:
        return False
    return notify_requested and not has_open_cause and not notification_already_enqueued


def _projection(
    incident: Incident, hospital_name: str, run_id: uuid.UUID | None, owner: str, sla: str
) -> IncidentSlackProjection:
    return IncidentSlackProjection(
        incident.id,
        hospital_name,
        incident.severity,
        incident.customer_impact,
        incident.next_action,
        incident.admin_path,
        owner,
        sla,
        incident.hospital_id,
        run_id,
        incident.version,
        incident.safe_error_message or _generation_safe_cause(incident.safe_error_code or ""),
        incident.episode_seq,
        incident_type=incident_type_of(incident),
    )


async def open_generation_incident(
    *,
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    hospital_name: str,
    run_id: uuid.UUID,
    code: str,
    message: str,
    notify: bool = True,
    subject_hash: str | None = None,
) -> uuid.UUID:
    sessions = get_async_sessionmaker()
    observed_at = datetime.now(UTC)
    async with sessions() as db:
        # 주제 교체 뒤의 실패는 새 epoch로 열려야 하므로 신원을 정하기 전에 이력을 읽는다.
        get_item = getattr(db, "get", None)
        swapped_item = await get_item(ContentItem, item_id) if get_item is not None else None
        topic_swap_count = len(getattr(swapped_item, "topic_swap_history", None) or [])
        object_type, object_id, admin_path = _incident_identity(
            code, item_id, hospital_id, subject_hash, topic_swap_count
        )
        # 중복 제거 키만 subject를 포함한다. source_id는 글 자체로 남겨 운영 큐 조인과
        # 성공 시 자동 종료가 subject와 무관하게 같은 글을 찾게 한다.
        source_id = object_id if object_type == "hospital" else str(item_id)
        dedupe_key = build_incident_key(
            "content_generation", object_type, object_id, _fingerprint(code)
        )
        previous = await db.scalar(select(Incident).where(Incident.dedupe_key == dedupe_key))
        previous_state = previous.state if previous is not None else None
        if (
            previous is not None
            and previous_state == IncidentState.ACKNOWLEDGED.value
            and getattr(previous, "safe_error_code", None) == code
        ):
            # 같은 슬롯·원인을 사람이 확인 완료한 episode는 유지한다. 다음 sweep이
            # ACK를 OPEN으로 되돌리거나 episode_seq를 올려 다시 paging하면 안 된다.
            return previous.id

        blocking_cause = None
        if code == "CONTENT_NOT_GENERATED":
            cause_states = {
                IncidentState.OPEN.value,
                IncidentState.RETRYING.value,
                IncidentState.ACKNOWLEDGED.value,
            }
            if (
                previous is not None
                and previous.state in cause_states
                and getattr(previous, "safe_error_code", None) != code
            ):
                # MISSING_REFERENCES shares the missing-prerequisite fingerprint with
                # CONTENT_NOT_GENERATED, so the exact cause may already be this row.
                blocking_cause = previous
            else:
                blocking_cause = await db.scalar(
                    select(Incident)
                    .where(
                        Incident.hospital_id == hospital_id,
                        Incident.state.in_(tuple(cause_states)),
                        Incident.safe_error_code != "CONTENT_NOT_GENERATED",
                        Incident.source_id.in_((str(item_id), str(hospital_id))),
                    )
                    .order_by(Incident.last_seen_at.desc(), Incident.id.desc())
                )
            if (
                blocking_cause is not None
                and blocking_cause.state == IncidentState.ACKNOWLEDGED.value
            ):
                return blocking_cause.id

        # The old implementation opened one incident per content item for this
        # hospital-level gate.  During the first rollout of the hospital-scoped
        # key, inherit any still-open legacy episode so the migration itself does
        # not page the operator again.
        if code == "MISSING_APPROVED_ESSENCE" and previous is None:
            legacy_open = await db.scalar(
                select(Incident).where(
                    Incident.hospital_id == hospital_id,
                    Incident.source_type == "CONTENT_GENERATION",
                    Incident.safe_error_code == code,
                    Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
                )
            )
            if legacy_open is not None:
                previous_state = legacy_open.state

        if blocking_cause is not None:
            incident = blocking_cause
            notification_code = incident.safe_error_code or code
        else:
            safe_cause = (
                safe_generation_rejection_message(message)
                if code == "GENERATION_REJECTED"
                else message
                if code == "CONTENT_AI_HARD_FINDING"
                else REFERENCES_OPERATOR_DECIDES_CAUSE
                if operator_decides_references(code, swapped_item)
                else _generation_safe_cause(code)
            )
            customer_impact, next_action = _generation_operator_copy(
                code,
                safe_cause,
                retry_at=(
                    announced_recovery_time(swapped_item, observed_at)
                    if code == "CONTENT_NOT_GENERATED" and swapped_item is not None
                    else None
                ),
                retry_unscheduled=(
                    code == "CONTENT_NOT_GENERATED"
                    and recovery_time_unannounced(swapped_item, observed_at)
                ),
                retry_releasable=operator_retry_writes_now(swapped_item, observed_at),
                retry_opens_at=operator_retry_opens_at(swapped_item, observed_at),
                environment_exhausted=environment_recovery_exhausted(code, swapped_item),
                unwritten=swapped_item is not None and not _has_body(swapped_item),
                recovery_owned=scheduled_recovery_owns_blocker(code, swapped_item),
            )
            incident = await open_or_touch_incident(
                db,
                IncidentOpenRequest(
                    pipeline="content_generation",
                    object_type=object_type,
                    object_id=object_id,
                    fingerprint=_fingerprint(code),
                    incident_type="CONTENT_GENERATION_FAILED",
                    severity=_generation_severity(code),
                    customer_impact=customer_impact,
                    source_type="CONTENT_GENERATION",
                    next_action=next_action,
                    admin_path=admin_path,
                    hospital_id=hospital_id,
                    operation_run_id=run_id,
                    source_id=source_id,
                    safe_error_code=code,
                    safe_error_message=safe_cause,
                ),
                actor="content-generation-worker",
                reason="generation attempt failed",
            )
            notification_code = code
        item = swapped_item
        if notification_code == "MISSING_APPROVED_ESSENCE":
            # This incident records system work. Human action is represented by
            # the separate snapshot-keyed ESCALATED incident, including its SLA.
            retrying = await mark_retrying(
                db,
                incident.id,
                expected_version=incident.version,
                actor="content-generation-worker",
                reason="source processing and essence auto-review own recovery",
            )
            if isinstance(retrying, Incident):
                incident = retrying
                incident.sla_due_at = None
                incident.severity = IncidentSeverity.MEDIUM
        elif generation_block_is_terminal(notification_code, item):
            # 종착 판정이 먼저다. 저장된 시도 기록이 이 원인을 종착으로 굳혔다면 어떤
            # 스윕도 다시 사지 않으므로 RETRYING이 될 수 없다. 이전 episode의 기한을
            # 물려받아 "아직 재시도 중"으로 보이지 않게 저장값까지 지운다. 살아 있는 claim이 잡은
            # 행(마지막 발행기의 사본 판정)은 워커가 시도 기록의 소유자라 쓰지 않는다 — 인시던트의
            # 기한만 지운다.
            if item is not None and not generation_claim_is_active(item, now=observed_at):
                drop_stored_retry_deadline(item, notification_code)
            incident.sla_due_at = None
            if blocking_cause is not None:
                _name_unwritten_cause_action(incident, item)
        elif scheduled_recovery_owns_blocker(notification_code, item):
            # 예산이 남아 있는 자동 복구는 사람의 할 일이 아니다. 소진 뒤에만 OPEN이 된다.
            deadline = scheduled_recovery_deadline(item, notification_code)
            retrying = await mark_retrying(
                db, incident.id, expected_version=incident.version,
                actor="content-generation-worker",
                reason="scheduled generation recovery owns retry",
            )
            if isinstance(retrying, Incident):
                incident = retrying
            incident.sla_due_at = deadline
            incident.severity = IncidentSeverity.MEDIUM
        elif blocking_cause is not None:
            _refresh_reused_cause(incident, item, code, observed_at)
        if blocking_cause is None:
            await _recover_superseded_generation_incidents(
                db,
                incident=incident,
                item_id=item_id,
                hospital_id=hospital_id,
                code=notification_code,
            )
        notification_due = notify and _morning_notification_due(
            code=notification_code,
            item=item,
            observed_at=observed_at,
        )
        notification = None
        notification_already_enqueued = False
        if notification_due:
            notification = build_open_incident_notification(
                _projection(
                    incident,
                    hospital_name,
                    run_id,
                    "병원 운영 담당자",
                    "예정 공개 전",
                ),
                settings.ADMIN_BASE_URL,
            )
            notification_already_enqueued = (
                await db.scalar(
                    select(NotificationOutbox.id).where(
                        NotificationOutbox.dedupe_key == notification.dedupe_key
                    )
                )
            ) is not None
        if _should_send_generation_notification(
            notify_requested=notification_due,
            previous_state=(
                incident.state
                if blocking_cause is not None
                else (
                    previous_state
                    if previous is not None
                    and getattr(previous, "safe_error_code", None) == notification_code
                    else None
                )
            ),
            code=notification_code,
            has_open_cause=False,
            notification_already_enqueued=notification_already_enqueued,
        ):
            assert notification is not None
            await enqueue_notification(db, notification)
        await db.commit()
        return incident.id


def _refresh_reused_cause(incident: Incident, item, code: str, observed_at: datetime) -> None:
    """원고 미생성 관측이 재사용한 앞선 원인의 기한·조치를 지금 시도 기록에 맞춘다.

    재사용한 인시던트는 앞선 시도가 남긴 기한과 조치를 들고 있다. 게이트가 시도 기록을 원고
    미생성(OPERATOR_REQUIRED, 기한 없음)으로 바꿨다면 그 기한의 스윕은 이 슬롯을 집지 않고,
    앞선 원인의 '기다리세요'도 더는 사실이 아니다. 스윕 소유 기록(주제 교체 등)이 남아 있으면
    그 기록의 다음 시도 시각이 새 기한이다.
    """

    attempt = _stored_generation_attempt(item) if item is not None else {}
    deadline = _stored_retry_deadline(attempt)
    if incident.state == IncidentState.RETRYING.value:
        # 다음 시도가 없으면 복구 창이 지금 닫혔다고 적는다 — 기한이 지난 RETRYING은 사람의 일이다.
        incident.sla_due_at = deadline or observed_at
    if attempt.get("reason") == code and deadline is None:
        # 사람이 할 일은 게이트 기록이 정한다.
        incident.next_action = generation_operator_action(code)
    else:
        _name_unwritten_cause_action(incident, item)


def _name_unwritten_cause_action(incident: Incident, item) -> None:
    """재사용한 원인이 원고 없는 글의 사람 결정 보류·HARD 지적이면 그 조치로 바꾼다.

    새 인시던트가 본문 유무로 고르는 두 문구(`_generation_operator_copy`의 `unwritten`)와 같다.
    저장된 시도 기록이 그 원인을 가리킬 때만이다. 부르는 곳은 종착 원인과, 스윕이 소유하지 않는
    원인(`scheduled_recovery_owns_blocker`가 거짓)뿐이라 이때 기록에 다음 시도 시각이 없다. 기록이
    다른 원인(주제 교체 등)이면 그 조치를, 본문이 있는 글은 종전 조치를 그대로 둔다.
    """

    if item is None or _has_body(item):
        return
    cause = getattr(incident, "safe_error_code", None)
    if _stored_generation_attempt(item).get("reason") != cause:
        return
    if operator_decides_references(cause, item):
        incident.next_action = REFERENCES_OPERATOR_DECIDES_UNWRITTEN_ACTION
    elif cause == "CONTENT_AI_HARD_FINDING" and not scheduled_recovery_owns_blocker(cause, item):
        # 새 인시던트와 같은 제한이다(`recovery_owned`). 호출 순서상 소유 원인은 여기 오지 않지만,
        # 오면 '다시 쓰이지 않고'가 거짓이므로 조치를 그대로 둔다.
        incident.next_action = CONTENT_AI_HARD_FINDING_UNWRITTEN_ACTION


async def _recover_superseded_generation_incidents(
    db, *, incident: Incident, item_id: uuid.UUID, hospital_id: uuid.UUID, code: str
) -> int:
    """Close this item's other open causes once a new one is recorded.

    한 글이 동시에 여러 원인으로 열려 있으면 운영센터가 같은 일을 여러 번 시킨다.
    지금 관측된 원인만 남기고 나머지는 회수한다(원인별 1건 계약).
    """

    execute = getattr(db, "execute", None)
    if execute is None:
        return 0
    superseded = list(
        (
            await execute(
                select(Incident).where(
                    Incident.hospital_id == hospital_id,
                    Incident.source_type == "CONTENT_GENERATION",
                    Incident.source_id == str(item_id),
                    Incident.id != incident.id,
                    Incident.safe_error_code != code,
                    Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
                )
            )
        ).scalars()
    )
    recovered = 0
    for stale in superseded:
        current = stale
        if current.state == IncidentState.OPEN:
            retrying = await mark_retrying(
                db,
                current.id,
                expected_version=current.version,
                actor="content-generation-worker",
                reason="another cause now blocks this slot",
            )
            if not isinstance(retrying, Incident):
                continue
            current = retrying
        result = await mark_recovered(
            db,
            current.id,
            expected_version=current.version,
            observed_success=True,
            actor="content-generation-worker",
            reason="superseded by a newer generation cause",
        )
        if isinstance(result, Incident):
            recovered += 1
    return recovered


async def recover_generation_incidents(
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    hospital_name: str,
    run_id: uuid.UUID | None,
    *,
    include_image: bool = True,
    safe_error_codes: tuple[str, ...] | None = None,
    dedupe_keys: tuple[str, ...] | None = None,
) -> int:
    """Close incidents from observed success without paging humans about healthy recovery.

    `dedupe_keys`로 신원을 좁히면 같은 글에 여러 건이 열려 있어도 성공이 증명한 그 건만
    닫는다. 재인증처럼 사람이 이미지 subject마다 따로 결정하는 사고에 필요하다.
    """

    sessions = get_async_sessionmaker()
    recovered = 0
    async with sessions() as db:
        source_scope = (Incident.source_id == str(item_id)) | (
            (Incident.safe_error_code == "MISSING_APPROVED_ESSENCE")
            & (Incident.source_id == str(hospital_id))
        )
        statement = select(Incident).where(
            Incident.hospital_id == hospital_id,
            Incident.source_type == "CONTENT_GENERATION",
            source_scope,
            Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
        )
        if dedupe_keys is not None:
            statement = statement.where(Incident.dedupe_key.in_(dedupe_keys))
        if safe_error_codes is not None:
            statement = statement.where(Incident.safe_error_code.in_(safe_error_codes))
        elif not include_image:
            statement = statement.where(
                Incident.safe_error_code.notin_(
                    ("IMAGE_GENERATION_FAILED", "CONTENT_IMAGE_NOT_READY")
                )
            )
        incidents = list((await db.execute(statement)).scalars())
        for incident in incidents:
            current = incident
            if current.state == IncidentState.OPEN:
                retrying = await mark_retrying(
                    db,
                    current.id,
                    expected_version=current.version,
                    actor="content-generation-worker",
                    reason="generation retry started",
                )
                if not isinstance(retrying, Incident):
                    continue
                current = retrying
            if run_id is not None:
                current.operation_run_id = run_id
            result = await mark_recovered(
                db,
                current.id,
                expected_version=current.version,
                observed_success=True,
                actor="content-generation-worker",
                reason="generation retry succeeded",
            )
            if not isinstance(result, Incident):
                continue
            recovered += 1
        await db.commit()
    return recovered
