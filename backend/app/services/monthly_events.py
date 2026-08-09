"""Monthly report truth projected as safe operational milestones."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import assert_never

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monthly_control import ReportArtifactState
from app.models.operations import NotificationOutbox
from app.services.notification_contracts import NotificationPayloadError
from app.services.notification_milestone_messages import (
    MilestoneKind,
    MilestoneProjection,
    build_milestone_action_notification,
    build_milestone_recovery_notification,
)
from app.services.notification_outbox import enqueue_notification


class MonthlyEventType(StrEnum):
    BLOCKED = "BLOCKED"
    ARTIFACT_VALIDATION_PENDING = "ARTIFACT_VALIDATION_PENDING"
    CUSTOMER_READY = "CUSTOMER_READY"
    DELIVERY_CORRECTED = "DELIVERY_CORRECTED"
    DELIVERY_RESCINDED = "DELIVERY_RESCINDED"
    DELIVERY_REDELIVERED = "DELIVERY_REDELIVERED"


@dataclass(frozen=True, slots=True)
class MonthlyEvent:
    event_id: uuid.UUID
    event_type: MonthlyEventType
    report_id: uuid.UUID
    hospital_id: uuid.UUID
    hospital_name: str
    period_year: int
    period_month: int
    quality: str
    planned_count: int
    success_count: int
    failed_count: int
    manifest_closed: bool
    artifact_state: ReportArtifactState
    doctor_artifact_id: uuid.UUID | None
    delivery_ready: bool
    blocker_codes: tuple[str, ...]
    owner_label: str
    sla_due_at: datetime | None
    occurred_at: datetime


def project_monthly_event(event: MonthlyEvent) -> MilestoneProjection:
    if event.occurred_at.tzinfo is None:
        raise NotificationPayloadError("MONTHLY_EVENT_TIME_REQUIRED")
    coverage_complete = (
        event.quality == "COMPLETE"
        and event.planned_count > 0
        and event.success_count == event.planned_count
        and event.failed_count == 0
        and event.manifest_closed
    )
    artifact_valid = (
        event.artifact_state is ReportArtifactState.VALID and event.doctor_artifact_id is not None
    )
    customer_ready = (
        coverage_complete and artifact_valid and event.delivery_ready and not event.blocker_codes
    )
    stable_id = f"milestone:v1:{event.event_id}"
    admin_path = f"/hospitals/{event.hospital_id}/reports?report={event.report_id}"
    sla_label = event.sla_due_at.isoformat() if event.sla_due_at is not None else "기한 없음"
    match event.event_type:
        case MonthlyEventType.CUSTOMER_READY:
            if not customer_ready:
                raise NotificationPayloadError("CUSTOMER_READY_GATE_BLOCKED")
            return MilestoneProjection(
                stable_id,
                MilestoneKind.MONTHLY_CUSTOMER_READY,
                event.hospital_id,
                event.hospital_name,
                "원장 전달본 검증 완료",
                "월간 리포트를 원장에게 전달할 수 있습니다.",
                "검증된 원장용 PDF를 확인하고 전달 기록을 남겨 주세요.",
                event.owner_label,
                sla_label,
                admin_path,
                False,
                False,
            )
        case MonthlyEventType.ARTIFACT_VALIDATION_PENDING:
            if not coverage_complete or artifact_valid or event.delivery_ready:
                raise NotificationPayloadError("ARTIFACT_PENDING_STATE_INVALID")
            return MilestoneProjection(
                stable_id,
                MilestoneKind.MONTHLY_ARTIFACT_PENDING,
                event.hospital_id,
                event.hospital_name,
                "내부 집계 완료 · 산출물 검증 대기",
                "측정은 완료됐지만 아직 고객 전달 가능 상태가 아닙니다.",
                "원장용 PDF를 생성하고 시각·폰트·파일 검증을 완료해 주세요.",
                event.owner_label,
                sla_label,
                admin_path,
                True,
                False,
            )
        case MonthlyEventType.BLOCKED:
            if customer_ready:
                raise NotificationPayloadError("MONTHLY_BLOCKED_STATE_INVALID")
            return MilestoneProjection(
                stable_id,
                MilestoneKind.MONTHLY_BLOCKED,
                event.hospital_id,
                event.hospital_name,
                "월간 리포트 차단",
                "월간 리포트를 아직 고객에게 전달할 수 없습니다.",
                "Admin에서 측정 실패와 현재 차단 사유를 해결해 주세요.",
                event.owner_label,
                sla_label,
                admin_path,
                True,
                False,
            )
        case MonthlyEventType.DELIVERY_CORRECTED:
            return MilestoneProjection(
                stable_id,
                MilestoneKind.DELIVERY_CORRECTED,
                event.hospital_id,
                event.hospital_name,
                "전달 기록 정정 완료",
                "잘못된 전달 정보가 정정됐습니다.",
                "정정된 전달 이력을 확인해 주세요.",
                event.owner_label,
                sla_label,
                admin_path,
                False,
                True,
                f"report:{event.report_id}:delivery",
            )
        case MonthlyEventType.DELIVERY_RESCINDED:
            return MilestoneProjection(
                stable_id,
                MilestoneKind.DELIVERY_RESCINDED,
                event.hospital_id,
                event.hospital_name,
                "리포트 전달 철회",
                "이전 전달 기록이 더 이상 유효하지 않습니다.",
                "철회 사유와 후속 재전달 필요 여부를 확인해 주세요.",
                event.owner_label,
                sla_label,
                admin_path,
                True,
                False,
            )
        case MonthlyEventType.DELIVERY_REDELIVERED:
            return MilestoneProjection(
                stable_id,
                MilestoneKind.DELIVERY_REDELIVERED,
                event.hospital_id,
                event.hospital_name,
                "리포트 재전달 완료",
                "검증된 원장용 PDF가 다시 전달됐습니다.",
                "최신 전달 이력을 확인해 주세요.",
                event.owner_label,
                sla_label,
                admin_path,
                False,
                True,
                f"report:{event.report_id}:delivery",
            )
        case unreachable:
            assert_never(unreachable)


async def enqueue_monthly_event(
    db: AsyncSession, event: MonthlyEvent, admin_base_url: str
) -> NotificationOutbox:
    """Enqueue action/recovery without committing or changing report truth."""

    projection = project_monthly_event(event)
    if projection.requires_action:
        intent = build_milestone_action_notification(projection, admin_base_url)
    elif projection.is_recovery:
        intent = build_milestone_recovery_notification(projection, admin_base_url)
    else:
        raise NotificationPayloadError("MILESTONE_SUMMARY_REQUIRED")
    return await enqueue_notification(db, intent, now=event.occurred_at)
