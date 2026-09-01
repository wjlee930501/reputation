"""Monthly report truth projected as safe operational milestones."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import assert_never

from app.models.monthly_control import ReportArtifactState
from app.services.notification_contracts import NotificationPayloadError
from app.services.notification_milestone_messages import (
    MilestoneKind,
    MilestoneProjection,
)
from app.services.notification_milestone_rendering import operator_deadline


class MonthlyEventType(StrEnum):
    BLOCKED = "BLOCKED"
    ARTIFACT_VALIDATION_PENDING = "ARTIFACT_VALIDATION_PENDING"
    CUSTOMER_READY = "CUSTOMER_READY"
    DELIVERY_CORRECTED = "DELIVERY_CORRECTED"
    DELIVERY_RESCINDED = "DELIVERY_RESCINDED"
    DELIVERY_REDELIVERED = "DELIVERY_REDELIVERED"


class MonthlyRunStage(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    COVERAGE_COMPLETE = "COVERAGE_COMPLETE"
    ARTIFACT_VALIDATION_PENDING = "ARTIFACT_VALIDATION_PENDING"
    ARTIFACT_VALIDATED = "ARTIFACT_VALIDATED"
    EXISTING = "EXISTING"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class MonthlyRunOperatorCopy:
    status_label: str
    what_happened: str
    customer_impact: str
    next_action: str


def monthly_run_operator_copy(stage: MonthlyRunStage) -> MonthlyRunOperatorCopy:
    """Translate a persisted execution stage into one actionable operator card."""
    match stage:
        case MonthlyRunStage.QUEUED:
            return MonthlyRunOperatorCopy(
                "리포트 생성 대기 중",
                "리포트 생성 요청이 순서대로 대기하고 있습니다.",
                "완료 전까지 원장님께 전달할 새 파일이 없습니다.",
                "잠시 기다린 뒤 이 화면에서 진행 상태를 다시 확인해 주세요.",
            )
        case MonthlyRunStage.RUNNING:
            return MonthlyRunOperatorCopy(
                "리포트를 만들고 있습니다",
                "측정 결과를 모아 월간 리포트를 만드는 중입니다.",
                "아직 원장님께 전달할 수 없습니다.",
                "완료될 때까지 기다린 뒤 새 리포트를 검수해 주세요.",
            )
        case MonthlyRunStage.BLOCKED:
            return MonthlyRunOperatorCopy(
                "필수 측정이 부족해 전달이 멈췄습니다",
                "리포트는 만들어졌지만 필수 측정이나 운영 자료가 부족합니다.",
                "현재 파일은 원장님께 전달할 수 없습니다.",
                "운영 센터에서 차단 사유를 확인하고 해결한 뒤 ‘리포트 다시 만들기’를 눌러 주세요.",
            )
        case MonthlyRunStage.COVERAGE_COMPLETE:
            return MonthlyRunOperatorCopy(
                "측정 집계가 완료됐습니다",
                "이번 달에 계획한 측정 결과가 모두 모였습니다.",
                "원장 전달용 PDF 확인이 끝나기 전에는 전달할 수 없습니다.",
                "원장 전달용 PDF 준비 상태를 이어서 확인해 주세요.",
            )
        case MonthlyRunStage.ARTIFACT_VALIDATION_PENDING:
            return MonthlyRunOperatorCopy(
                "원장 전달용 PDF 확인이 필요합니다",
                "측정 집계와 리포트 생성은 끝났지만 원장 전달용 PDF 확인이 남았습니다.",
                "확인 전 파일은 원장님께 전달할 수 없습니다.",
                "원장 전달용 PDF를 열어 글자·페이지·내용을 확인해 주세요.",
            )
        case MonthlyRunStage.ARTIFACT_VALIDATED:
            return MonthlyRunOperatorCopy(
                "원장 전달용 PDF 검증 완료",
                "원장 전달용 PDF의 한 페이지 구성, 한글, 필수 안내와 링크를 확인했습니다.",
                "최종 전달 가능 여부는 최신 병원 자료와 공개 상태를 함께 확인해야 합니다.",
                "리포트 화면에서 최신 자료와 전달 가능 상태를 확인해 주세요.",
            )
        case MonthlyRunStage.EXISTING:
            return MonthlyRunOperatorCopy(
                "기존 리포트가 있습니다",
                "같은 기간의 리포트가 있어 중복 생성을 건너뛰었습니다.",
                "기존 리포트는 그대로 보존됩니다.",
                "기존 리포트를 검수하거나 변경 사항이 있으면 ‘리포트 다시 만들기’를 눌러 주세요.",
            )
        case MonthlyRunStage.FAILED:
            return MonthlyRunOperatorCopy(
                "리포트를 만들지 못했습니다",
                "월간 리포트를 끝까지 만들지 못했습니다.",
                "해당 월의 새 리포트를 원장님께 전달할 수 없습니다.",
                "‘리포트 다시 만들기’를 눌러 주세요. 다시 실패하면 ‘개발팀 문의용 정보 복사’로 전달해 주세요.",
            )
        case unreachable:
            assert_never(unreachable)


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
    sla_label = operator_deadline(event.sla_due_at)
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
                "측정 집계 완료 · 원장 전달용 PDF 확인 대기",
                "측정은 완료됐지만 아직 고객 전달 가능 상태가 아닙니다.",
                "원장 전달용 PDF를 열어 글자·페이지·내용을 확인해 주세요.",
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
                "리포트 화면에서 차단 사유를 확인해 해결한 뒤 ‘리포트 다시 만들기’를 눌러 "
                "주세요. 다시 실패하면 ‘개발팀 문의용 정보 복사’를 눌러 개발팀에 전달해 주세요.",
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
                "전달 정보 수정 기록 추가",
                "수정 기록이 추가됐으며 실제 전달 여부는 이 기록만으로 확인되지 않습니다.",
                "수정된 전달 기록을 확인해 주세요.",
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
                "전달 기록 무효 처리",
                "기존 전달 기록은 무효가 됐지만 이미 보낸 파일은 회수되지 않습니다.",
                "무효 처리 사유를 확인하고 이미 보낸 파일은 별도로 사용 중지를 안내해 주세요.",
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
                "재전달 기록 추가",
                "재전달 운영 기록이 추가됐으며 실제 수신 여부는 별도로 확인해야 합니다.",
                "재전달 기록과 원장 수신 여부를 확인해 주세요.",
                event.owner_label,
                sla_label,
                admin_path,
                False,
                True,
                f"report:{event.report_id}:delivery",
            )
        case unreachable:
            assert_never(unreachable)
