"""Admin operations 제어 평면 스키마 — 비용 가드, 후행 확인 대기 큐."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CostGuardCategoryUsage(BaseModel):
    category: str
    label: str
    daily_used: int | None
    daily_limit: int
    # 설정 기본값. daily_limit과 다르면 오늘치 임시 상향이 걸려 있다는 뜻이다.
    daily_limit_default: int = 0
    monthly_used: int | None
    monthly_limit: int
    # 예약 수(*_used)와 실제 공급자 호출 수(*_actual)가 벌어지면 재시도 증폭 신호다.
    daily_actual: int | None = None
    monthly_actual: int | None = None


class CostGuardStatusResponse(BaseModel):
    availability: Literal["AVAILABLE", "UNAVAILABLE"] = "AVAILABLE"
    enabled: bool
    kill_switch_active: bool | None
    categories: list[CostGuardCategoryUsage]


class CostGuardKillSwitchRequest(BaseModel):
    enabled: bool


class CostGuardKillSwitchResponse(BaseModel):
    kill_switch_active: bool


class CostGuardDailyLimitRequest(BaseModel):
    category: str
    # None이면 오늘치 상향을 해제하고 설정 기본값으로 되돌린다.
    limit: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=3, max_length=200)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("한도 변경 사유를 3자 이상 입력해 주세요.")
        return cleaned


class CostGuardDailyLimitResponse(BaseModel):
    category: str
    daily_limit: int
    daily_limit_default: int


class AttentionHospital(BaseModel):
    """공개됐지만 아직 사람이 확인하지 않은 콘텐츠가 있는 병원 한 곳."""

    hospital_id: UUID
    hospital_name: str
    unreviewed_count: int
    overdue_count: int
    oldest_published_at: datetime | None


class AttentionReportHospital(BaseModel):
    hospital_id: UUID
    hospital_name: str
    # 리포트가 아예 없으면 None. 있으면 전달 표시가 안 된 리포트의 id.
    report_id: UUID | None = None


class AttentionReports(BaseModel):
    """지난달 원장 보고가 빠진 곳. 월말 배치 실패는 다음 달에야 드러난다."""

    period_year: int
    period_month: int
    missing: list[AttentionReportHospital]
    undelivered: list[AttentionReportHospital]


class AttentionQueueResponse(BaseModel):
    unreviewed_total: int
    overdue_total: int
    overdue_hours: int
    hospitals: list[AttentionHospital]
    reports: AttentionReports


class OperationsQueue(StrEnum):
    ONBOARDING = "ONBOARDING"
    TODAY = "TODAY"
    REPORTS = "REPORTS"
    INCIDENTS = "INCIDENTS"


class OperationsSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OperationsOwner(OperationsSchema):
    id: UUID
    name: str
    email: str


class OperationsCustomer(OperationsSchema):
    hospital_id: UUID | None
    name: str
    admin_path: str


class OperationsAction(OperationsSchema):
    kind: str
    label: str
    method: str
    path: str
    enabled: bool = True
    reason_required: bool = False
    requires_version: bool = False
    requires_idempotency_key: bool = False


class OperationsHistoryEntry(OperationsSchema):
    event: str
    at: datetime
    actor: str | None = None


class OperationsSlackState(OperationsSchema):
    notification_id: UUID
    notification_type: str
    state: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    sent_at: datetime | None
    safe_error_code: str | None
    safe_error_message: str | None
    version: int


class OperationsRunSummary(OperationsSchema):
    run_id: UUID
    parent_run_id: UUID | None
    operation_type: str
    state: str
    attempt_count: int
    total_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    safe_error_code: str | None
    safe_error_message: str | None
    requested_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    version: int
    retry: OperationsAction | None


class OperationsQueueRow(OperationsSchema):
    id: str
    queue: OperationsQueue
    customer: OperationsCustomer
    status: str
    severity: str
    impact: str
    owner: OperationsOwner | None
    sla_due_at: datetime | None
    sla_state: str
    next_action: str
    action: OperationsAction
    retry: OperationsAction | None
    cause_code: str | None = None
    cause_message: str | None = None
    cause_group_key: str | None = None
    same_type_count: int = 1
    affected_hospital_count: int = 0
    cost_guard_category: str | None = None
    # False marks a row that is context, not work: automatic recovery owns it right
    # now, or the normal schedule has not reached it yet. The row stays in the
    # response so the FE can collapse it instead of losing it.
    requires_operator_action: bool = True
    safe_cause: str | None
    history: list[OperationsHistoryEntry]
    slack: OperationsSlackState | None
    incident_id: UUID | None = None
    operation_run_id: UUID | None = None
    content_id: UUID | None = None
    report_id: UUID | None = None
    version: int | None = None
    occurred_at: datetime


class OperationsQueueSummary(OperationsSchema):
    queue: OperationsQueue
    total: int


class OperationsQueueResponse(OperationsSchema):
    queue: OperationsQueue
    total: int
    page: int
    page_size: int
    items: list[OperationsQueueRow]


class OperationsOverviewResponse(OperationsSchema):
    queues: list[OperationsQueueSummary]
    items: list[OperationsQueueRow]


class IncidentDetailResponse(OperationsSchema):
    incident: OperationsQueueRow
    run: OperationsRunSummary | None


class VersionedReasonRequest(OperationsSchema):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=200)


class IncidentAssignRequest(VersionedReasonRequest):
    owner_id: UUID | None
    sla_due_at: datetime | None


class OperationRetryRequest(OperationsSchema):
    reason: str = Field(min_length=3, max_length=200)


class NotificationRetryRequest(VersionedReasonRequest):
    pass
