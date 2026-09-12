"""Incident and Slack-outbox projection for content generation failures."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
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
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification
from app.workers.generation_retry_policy import (
    GenerationRetryClass,
    next_recovery_sweep,
    repair_session_is_available,
)
from app.workers.generation_run_control import safe_generation_rejection_message

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
_AUTOMATIC_BODY_REPAIR_CODES = frozenset(
    {
        "FAQ_FIELDS_MISSING",
        "MISSING_REFERENCES",
        "FORBIDDEN_EXPRESSION",
        "ESSENCE_NOT_ALIGNED",
        "CONTENT_AI_REVIEW_STALE",
    }
)
_BODY_REPAIR_STATE_KEY = "automatic_body_repair"
_GENERATION_ATTEMPT_KEY = "generation_attempt"
# One Slack digest per morning batch replaces the per-content-item pages.
PREPUBLISH_MORNING_BATCH = "PREPUBLISH_0745"
PUBLISH_MORNING_BATCH = "PUBLISH_0800"


def _stored_generation_attempt(item) -> dict:
    summary = getattr(item, "essence_check_summary", None)
    attempt = summary.get(_GENERATION_ATTEMPT_KEY) if isinstance(summary, dict) else None
    return dict(attempt) if isinstance(attempt, dict) else {}


def scheduled_recovery_owns_blocker(code: str, item) -> bool:
    """Return whether a sweep still owns this cause, so it is not operator work.

    예산이 남아 있는 동안의 자동 복구는 RETRYING이다. 예산이 소진돼
    OPERATOR_REQUIRED로 전이한 뒤에야 원인별 인시던트 1건이 OPEN으로 열린다.
    """

    attempt = _stored_generation_attempt(item)
    if (
        attempt.get("reason") == code
        and attempt.get("retry_class") == GenerationRetryClass.OPERATOR_REQUIRED.value
    ):
        return False
    if code in _AUTOMATIC_BODY_REPAIR_CODES:
        summary = getattr(item, "essence_check_summary", None)
        state = summary.get(_BODY_REPAIR_STATE_KEY) if isinstance(summary, dict) else None
        return repair_session_is_available(state)
    if code in _AUTOMATIC_RECOVERY_CODES:
        return True
    return (
        attempt.get("reason") == code
        and attempt.get("retry_class") == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    )


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


def generation_safe_cause(code: str) -> str:
    """Operator-safe Korean cause for one generation blocker code."""

    return _generation_safe_cause(code)


def generation_operator_action(code: str) -> str:
    """Operator-safe Korean next action for one generation blocker code."""

    return _generation_operator_copy(code)[1]


def _generation_operator_copy(code: str) -> tuple[str, str]:
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
        "CONTENT_NOT_GENERATED": (
            "운영 센터에서 해당 항목의 “작업 다시 시도”를 누르세요. 자동 복구는 "
            "01시·04시·07시·07시 45분에도 다시 실행됩니다."
        ),
        "MISSING_REFERENCES": ("운영 센터에서 콘텐츠 주제와 승인된 참고 자료를 확인하세요."),
        "FORBIDDEN_EXPRESSION": (
            "운영 센터에서 의료광고 금지 표현이 차단된 공개 필드와 승인된 대체 문구를 확인하세요."
        ),
        "ESSENCE_NOT_ALIGNED": ("병원 온보딩에서 승인된 운영 기준과 차단된 문구를 확인하세요."),
        "FAQ_FIELDS_MISSING": ("운영 센터에서 FAQ 질문과 직접 답변 요약의 누락 원인을 확인하세요."),
        "CONTENT_AI_HARD_FINDING": (
            "운영 센터에서 미해결 사실·의료 안전 지적과 승인 자료를 확인하세요."
        ),
        "CONTENT_AI_REVIEW_STALE": ("운영 센터에서 변경된 원고와 재검수 대기 상태를 확인하세요."),
        "CONTENT_AI_REVIEW_UNAVAILABLE": (
            "시스템 재시도 중입니다. 다음 예약 배치가 독립 검수를 다시 실행합니다."
        ),
        "CONTENT_AI_REVIEW_CONFIG_ERROR": (
            "독립 검수 공급자 인증과 모델 설정을 확인하세요. 자동 재시도 대상이 아닙니다."
        ),
        "CONTENT_IMAGE_NOT_READY": (
            "시스템 재시도 중입니다. 다음 예약 배치가 대표 이미지를 다시 생성합니다."
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
        "MISSING_REFERENCES": "의료 콘텐츠에 필요한 참고 자료가 준비되지 않았습니다.",
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


def _incident_identity(
    code: str,
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    subject_hash: str | None = None,
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
    return "content_item", str(item_id), "/operations"


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
    async with sessions() as db:
        object_type, object_id, admin_path = _incident_identity(
            code, item_id, hospital_id, subject_hash
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
            customer_impact, next_action = _generation_operator_copy(code)
            safe_cause = (
                safe_generation_rejection_message(message)
                if code == "GENERATION_REJECTED"
                else message
                if code == "CONTENT_AI_HARD_FINDING"
                else _generation_safe_cause(code)
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
        observed_at = datetime.now(UTC)
        get_item = getattr(db, "get", None)
        item = await get_item(ContentItem, item_id) if get_item is not None else None
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
        elif scheduled_recovery_owns_blocker(notification_code, item):
            # 예산이 남아 있는 자동 복구는 사람의 할 일이 아니다. 소진 뒤에만 OPEN이 된다.
            retrying = await mark_retrying(
                db, incident.id, expected_version=incident.version,
                actor="content-generation-worker",
                reason="scheduled generation recovery owns retry",
            )
            if isinstance(retrying, Incident):
                incident = retrying
            incident.sla_due_at = next_recovery_sweep()
            incident.severity = IncidentSeverity.MEDIUM
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
