"""Admin operations 제어 평면 스키마 — 비용 가드, 후행 확인 대기 큐."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CostGuardCategoryUsage(BaseModel):
    category: str
    label: str
    daily_used: int
    daily_limit: int
    # 설정 기본값. daily_limit과 다르면 오늘치 임시 상향이 걸려 있다는 뜻이다.
    daily_limit_default: int = 0
    monthly_used: int
    monthly_limit: int
    # 예약 수(*_used)와 실제 공급자 호출 수(*_actual)가 벌어지면 재시도 증폭 신호다.
    daily_actual: int = 0
    monthly_actual: int = 0


class CostGuardStatusResponse(BaseModel):
    enabled: bool
    kill_switch_active: bool
    categories: list[CostGuardCategoryUsage]


class CostGuardKillSwitchRequest(BaseModel):
    enabled: bool


class CostGuardKillSwitchResponse(BaseModel):
    kill_switch_active: bool


class CostGuardDailyLimitRequest(BaseModel):
    category: str
    # None이면 오늘치 상향을 해제하고 설정 기본값으로 되돌린다.
    limit: int | None = Field(default=None, gt=0)


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
