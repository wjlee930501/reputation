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
    name = safe_text(hospital_name, 100)
    measurement = _measurement_label(sov_pct, platforms)
    message = validated_message(
        RenderedSlackMessage(
            fallback_text=(
                f"무슨 문제인지: {name} 초기 진단 리포트 준비 완료 · "
                "고객 영향: 원장 전달 전 검수 필요 · "
                "지금 할 일: Admin에서 V0 리포트 검토 · 처리 기한: 원장 보고 전"
            ),
            blocks=(
                header_block("onboarding_v0_header", "V0 초기 진단 준비 완료"),
                section_block("onboarding_v0_identity", f"*{name}*"),
                section_block(
                    "onboarding_v0_context",
                    (
                        "무슨 문제인지: 초기 AI 노출 진단 리포트가 준비되었습니다.\n"
                        "고객 영향: 원장에게 전달하기 전에 측정 결과와 설명 근거를 검수해야 합니다.\n"
                        f"현재 확인: {measurement}\n"
                        "지금 할 일: Admin에서 V0 리포트를 검토한 뒤 원장에게 전달해 주세요.\n"
                        "처리 기한: 원장 보고 전"
                    ),
                ),
                action_block("onboarding_v0_action", url, "V0 리포트 검토"),
            ),
            admin_url=url,
        ),
        admin_base_url,
    )
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
    admin_base_url: str = settings.ADMIN_BASE_URL,
) -> NotificationIntent:
    """Build one activation action, identified by the hospital's site milestone."""

    url = admin_url(admin_base_url, f"/hospitals/{hospital_id}/profile#domain-setup")
    name = safe_text(hospital_name, 100)
    message = validated_message(
        RenderedSlackMessage(
            fallback_text=(
                f"무슨 문제인지: {name} 콘텐츠 허브 준비 완료 · "
                "고객 영향: 공개 주소 확인 전에는 운영 미활성 · "
                "지금 할 일: Admin에서 공개 주소 상태 확인 · 처리 기한: 오늘 중"
            ),
            blocks=(
                header_block("onboarding_site_header", "콘텐츠 허브 준비 완료"),
                section_block("onboarding_site_identity", f"*{name}*"),
                section_block(
                    "onboarding_site_context",
                    (
                        "무슨 문제인지: 병원 정보와 콘텐츠 허브의 공개 준비가 완료되었습니다.\n"
                        "고객 영향: 공개 주소를 확인하기 전에는 환자 대상 운영이 활성화되지 않습니다.\n"
                        "지금 할 일: Admin에서 기본 주소 또는 연결 도메인의 상태를 확인해 주세요.\n"
                        "처리 기한: 오늘 중"
                    ),
                ),
                action_block("onboarding_site_action", url, "공개 주소 상태 확인"),
            ),
            admin_url=url,
        ),
        admin_base_url,
    )
    return NotificationIntent(
        dedupe_key=f"ONBOARDING_SITE_BUILT:{hospital_id}:v1",
        notification_type=SITE_BUILT_NOTIFICATION_TYPE,
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
