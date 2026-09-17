"""Durable Slack intents for onboarding milestones committed by sync workers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.operations import NotificationOutbox, NotificationOutboxState
from app.services.notification_contracts import (
    NotificationIntent,
    NotificationPayloadError,
    validate_message,
)
from app.services.notification_milestone_rendering import (
    RenderedSlackMessage,
    action_block,
    admin_url,
    header_block,
    safe_text,
    section_block,
    validated_message,
)

V0_READY_NOTIFICATION_TYPE = "ONBOARDING_V0_READY"
SITE_BUILT_NOTIFICATION_TYPE = "ONBOARDING_SITE_BUILT"
ACTIVATED_NOTIFICATION_TYPE = "ONBOARDING_HOSPITAL_ACTIVATED"


def build_v0_ready_notification(
    *,
    hospital_id: uuid.UUID,
    hospital_name: str,
    report_id: uuid.UUID,
    sov_pct: float | None,
    platforms: list[str],
    admin_base_url: str = settings.ADMIN_BASE_URL,
) -> NotificationIntent:
    """Build one safe V0-review action, identified by the persisted report."""

    url = admin_url(admin_base_url, f"/hospitals/{hospital_id}/reports")
    name = safe_text(hospital_name, 90)
    measurement = _measurement_label(sov_pct, platforms)
    message = _onboarding_message("[전달 준비] 초기 진단 레포트", name,
        f"{measurement}\n고객용 파일과 설명을 확인해 원장님께 전달해 주세요. 아직 고객에게 자동 발송한 것은 아닙니다.",
        url, "초기 진단 레포트 열기", admin_base_url)
    return NotificationIntent(
        dedupe_key=f"ONBOARDING_V0_READY:{report_id}",
        notification_type=V0_READY_NOTIFICATION_TYPE,
        message=message,
        hospital_id=hospital_id,
    )


def build_site_built_notification(
    *,
    hospital_id: uuid.UUID,
    hospital_name: str,
    blocked_reason: str | None = None,
    admin_base_url: str = settings.ADMIN_BASE_URL,
) -> NotificationIntent:
    """Build one activation action, identified by the hospital's site milestone.

    기본 플랫폼 주소는 허브 준비가 끝나면 시스템이 그대로 운영을 시작하므로, 이 알림은
    자동 활성화가 불가능했던 병원에만 남는다. ``blocked_reason``은 왜 사람 손이 필요한지
    (자기 도메인 대기·선행 조건 미충족 등)를 그 자리에서 말한다.
    """

    url = admin_url(admin_base_url, f"/hospitals/{hospital_id}/info#domain-setup")
    name = safe_text(hospital_name, 90)
    reason = safe_text(blocked_reason, 180) if blocked_reason else "공개 주소를 확인해야 합니다."
    message = _onboarding_message("[조치 필요] 병원 공개 주소 확인", name,
        f"{reason}\n병원 정보의 공개 주소 상태를 확인해 주세요. 아직 공개 운영을 시작하지 못했습니다.",
        url, "공개 주소 설정 열기", admin_base_url)
    return NotificationIntent(
        dedupe_key=f"ONBOARDING_SITE_BUILT:{hospital_id}:v1",
        notification_type=SITE_BUILT_NOTIFICATION_TYPE,
        message=message,
        hospital_id=hospital_id,
    )


def build_hospital_activated_notification(
    *,
    hospital_id: uuid.UUID,
    hospital_name: str,
    public_url: str,
    admin_base_url: str = settings.ADMIN_BASE_URL,
) -> NotificationIntent:
    """자동 활성화 한 건에 알림 한 건. SITE_BUILT 재촉 알림을 더하지 않고 대체한다.

    dedupe_key가 병원 단위이므로 허브 준비 태스크가 다시 돌아도 두 번 나가지 않는다.
    """

    url = admin_url(admin_base_url, f"/hospitals/{hospital_id}")
    name = safe_text(hospital_name, 90)
    address = safe_text(public_url, 200)
    message = _onboarding_message("[운영 시작] 병원 공개 설정 완료", name,
        f"공개 주소: {address}\n추가 조치가 필요하지 않습니다. 정기 작업과 장애 감시는 시스템이 진행합니다.",
        url, "병원 운영 현황 보기", admin_base_url)
    return NotificationIntent(
        dedupe_key=f"ONBOARDING_HOSPITAL_ACTIVATED:{hospital_id}:v1",
        notification_type=ACTIVATED_NOTIFICATION_TYPE,
        message=message,
        hospital_id=hospital_id,
    )


def enqueue_onboarding_notification_sync(
    db: Session,
    intent: NotificationIntent,
    *,
    now: datetime | None = None,
) -> NotificationOutbox:
    """Insert an outbox row without committing the caller's domain transaction."""

    validate_message(intent.message, allowed_admin_base_url=settings.ADMIN_BASE_URL)
    if not intent.dedupe_key.strip() or intent.max_attempts < 1:
        raise NotificationPayloadError("INVALID_NOTIFICATION_INTENT")
    created_at = now or datetime.now(UTC)
    statement = (
        insert(NotificationOutbox)
        .values(
            id=uuid.uuid4(),
            hospital_id=intent.hospital_id,
            incident_id=intent.incident_id,
            operation_run_id=intent.operation_run_id,
            dedupe_key=intent.dedupe_key,
            notification_type=intent.notification_type,
            channel=intent.channel,
            state=NotificationOutboxState.PENDING.value,
            payload=intent.message.payload(),
            fallback_text=intent.message.fallback_text,
            max_attempts=intent.max_attempts,
            next_attempt_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        .on_conflict_do_nothing(index_elements=[NotificationOutbox.dedupe_key])
        .returning(NotificationOutbox)
    )
    row = db.execute(statement).scalar_one_or_none()
    if row is not None:
        return row
    return db.execute(
        select(NotificationOutbox).where(NotificationOutbox.dedupe_key == intent.dedupe_key)
    ).scalar_one()


def _measurement_label(sov_pct: float | None, platforms: list[str]) -> str:
    platform_labels = {"chatgpt": "ChatGPT", "gemini": "Gemini"}
    measured = tuple(
        dict.fromkeys(
            platform_labels.get(platform.lower(), "측정 서비스 확인 필요")
            for platform in platforms
        )
    )
    platform_text = " · ".join(measured) or "측정 서비스 확인 필요"
    sov_text = "확인 필요" if sov_pct is None else f"{sov_pct:.1f}%"
    return f"AI 답변 내 병원 언급률 {sov_text} · 측정 대상 {platform_text}"


def _onboarding_message(title, name, body, url, button, base):
    return validated_message(RenderedSlackMessage(
        f"{title} · {name} | {body}",
        (header_block("onboarding_header", title),
         section_block("onboarding_body", f"*{name}*\n{body}"),
         action_block("onboarding_action", url, button)), url), base)
