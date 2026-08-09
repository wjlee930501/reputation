"""Onboarding state transitions projected as safe operational milestones."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import assert_never

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import NotificationOutbox
from app.services.notification_contracts import NotificationPayloadError
from app.services.notification_milestone_messages import (
    MilestoneKind,
    MilestoneProjection,
    build_milestone_action_notification,
    build_milestone_recovery_notification,
)
from app.services.notification_outbox import enqueue_notification


class OnboardingEventType(StrEnum):
    HANDOFF_OVERDUE = "HANDOFF_OVERDUE"
    HANDOFF_ACCEPTED = "HANDOFF_ACCEPTED"
    ACTIVATION_READY = "ACTIVATION_READY"
    HOSPITAL_ACTIVE = "HOSPITAL_ACTIVE"


@dataclass(frozen=True, slots=True)
class OnboardingEvent:
    event_id: uuid.UUID
    event_type: OnboardingEventType
    hospital_id: uuid.UUID
    hospital_name: str
    owner_label: str
    occurred_at: datetime
    sla_due_at: datetime | None = None
    recovered_from_event_id: uuid.UUID | None = None


def project_onboarding_event(event: OnboardingEvent) -> MilestoneProjection:
    if event.occurred_at.tzinfo is None:
        raise NotificationPayloadError("ONBOARDING_EVENT_TIME_REQUIRED")
    stable_id = f"milestone:v1:{event.event_id}"
    sla_label = event.sla_due_at.isoformat() if event.sla_due_at is not None else "기한 없음"
    recovery_of = (
        f"milestone:v1:{event.recovered_from_event_id}"
        if event.recovered_from_event_id is not None
        else None
    )
    match event.event_type:
        case OnboardingEventType.HANDOFF_OVERDUE:
            if event.sla_due_at is None or event.sla_due_at.tzinfo is None:
                raise NotificationPayloadError("HANDOFF_OVERDUE_SLA_REQUIRED")
            if event.sla_due_at >= event.occurred_at:
                raise NotificationPayloadError("HANDOFF_NOT_OVERDUE")
            return MilestoneProjection(
                stable_id,
                MilestoneKind.HANDOFF_OVERDUE,
                event.hospital_id,
                event.hospital_name,
                "고객 인계 기한 초과",
                "온보딩 시작이 지연될 수 있습니다.",
                "계약·담당자 정보를 확인하고 고객 인계를 승인해 주세요.",
                event.owner_label,
                sla_label,
                "/operations",
                True,
                False,
            )
        case OnboardingEventType.HANDOFF_ACCEPTED:
            return MilestoneProjection(
                stable_id,
                MilestoneKind.HANDOFF_ACCEPTED,
                event.hospital_id,
                event.hospital_name,
                "고객 인계 승인",
                "온보딩 작업을 시작할 수 있습니다.",
                "Admin 온보딩 체크리스트를 진행해 주세요.",
                event.owner_label,
                sla_label,
                "/operations",
                False,
                recovery_of is not None,
                recovery_of,
            )
        case OnboardingEventType.ACTIVATION_READY:
            return MilestoneProjection(
                stable_id,
                MilestoneKind.ACTIVATION_READY,
                event.hospital_id,
                event.hospital_name,
                "공개 활성화 준비 완료",
                "모든 온보딩 선행 조건이 충족됐습니다.",
                "도메인 상태를 확인하고 공개 활성화를 진행해 주세요.",
                event.owner_label,
                sla_label,
                "/operations",
                True,
                False,
            )
        case OnboardingEventType.HOSPITAL_ACTIVE:
            return MilestoneProjection(
                stable_id,
                MilestoneKind.HOSPITAL_ACTIVE,
                event.hospital_id,
                event.hospital_name,
                "운영 활성화 완료",
                "병원이 자율 운영 대상으로 전환됐습니다.",
                "정기 운영 요약에서 상태를 확인해 주세요.",
                event.owner_label,
                sla_label,
                "/operations",
                False,
                False,
            )
        case unreachable:
            assert_never(unreachable)


async def enqueue_onboarding_event(
    db: AsyncSession, event: OnboardingEvent, admin_base_url: str
) -> NotificationOutbox:
    """Enqueue action/recovery without committing or changing onboarding state."""

    projection = project_onboarding_event(event)
    if projection.requires_action:
        intent = build_milestone_action_notification(projection, admin_base_url)
    elif projection.is_recovery:
        intent = build_milestone_recovery_notification(projection, admin_base_url)
    else:
        raise NotificationPayloadError("MILESTONE_SUMMARY_REQUIRED")
    return await enqueue_notification(db, intent, now=event.occurred_at)
