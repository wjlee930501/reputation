from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class DoctorArtifactProjection(BaseModel):
    """Safe doctor-artifact facts exposed only by the report detail API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["MISSING", "INVALID", "VALID"]
    state_label: Literal[
        "원장 전달용 PDF가 없습니다",
        "원장 전달용 PDF를 다시 만들어야 합니다",
        "원장 전달용 PDF 검증 완료",
    ]
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    byte_size: int | None = Field(default=None, gt=0)
    # 본문 1쪽 + (부록이 있으면) 2쪽. 그 외 쪽수는 검증을 통과하지 못한다.
    page_count: Literal[1, 2] | None = None
    validated_at: datetime | None = None
    validation_version: Literal["doctor-pdf-v1"] | None = None


class ReportMeasurementProjection(BaseModel):
    """Frozen measurement counts and operator copy for one report version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    quality: Literal["COMPLETE", "DEGRADED", "BLOCKED", "LEGACY_UNVERIFIED"]
    quality_label: str
    planned_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    problem: str
    customer_impact: str
    next_action: str


class ReportNotificationProjection(BaseModel):
    """Safe Slack evidence; transport payload and internal identifiers stay private."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal[
        "PENDING",
        "SENDING",
        "RETRYING",
        "HOLD",
        "SENT",
        "FAILED",
        "NOT_INDIVIDUALLY_LINKED",
    ]
    state_label: str
    problem: str
    customer_impact: str
    next_action: str
    sent_at: datetime | None = None
    operations_url: str


class ReportReviewEvidence(BaseModel):
    """Detail-only evidence needed before customer delivery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    version_label: str
    supersedes_report_id: str | None = None
    measurement: ReportMeasurementProjection
    notification: ReportNotificationProjection


class ReportListResponse(BaseModel):
    id: str
    hospital_id: str
    period_year: int
    period_month: int
    report_type: str
    display: Optional[dict[str, Any]] = None
    has_pdf: bool
    # 검증본에 묶인 전달 기록 파이프라인의 대상인지(월간만 true). 초기 진단(V0)은
    # AE가 PDF를 직접 원장에게 전달하므로 전달 이벤트를 남기지 않는다.
    delivery_tracked: bool = True
    # 원장 보고용 1페이지 판본이 준비됐는지. 없으면 화면이 그 버튼을 감춘다.
    has_doctor_pdf: bool = False
    doctor_artifact_state: Literal["MISSING", "INVALID", "VALID"] = "MISSING"
    doctor_artifact_sha256: Optional[str] = None
    download_url: Optional[str] = None
    sov_summary: Optional[Any]
    content_summary: Optional[Any]
    essence_summary: Optional[Any] = None
    delivery_ready: bool = False
    customer_ready: bool = False
    # 전달을 막은 이유 **전부**. 예전에는 게이트가 여러 이유로 막혀도 첫 줄 하나만
    # 담아, AE가 하나를 고쳐 재생성한 뒤에야 다음 이유를 알 수 있었다. 첫 원소는
    # 여전히 대표 사유이므로 화면의 `deliveryBlockers[0]` 표시는 그대로 동작한다.
    delivery_blockers: list[str] = Field(default_factory=list)
    # 전달을 막지 않는 경고(약정 미달, 사후검수 표본 미완료, 운영 기준 버전 갱신 등).
    # blockers와 달리 mark-sent를 막지 않는다 — Admin은 소프트 스타일로만 표시한다.
    delivery_warnings: list[str] = Field(default_factory=list)
    effective_delivery: Optional[dict[str, Any]] = None
    delivery_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    sent_at: Optional[str]


class ReportResponse(ReportListResponse):
    doctor_artifact: Optional[DoctorArtifactProjection] = None
    review_evidence: Optional[ReportReviewEvidence] = None


class ReportDeliveryRequest(BaseModel):
    artifact_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    recipient_label: str = Field(min_length=1, max_length=255)
    channel: str = Field(min_length=1, max_length=100)
    note: Optional[str] = Field(default=None, max_length=1000)


class ReportDeliveryCorrectionRequest(ReportDeliveryRequest):
    reason: str = Field(min_length=2, max_length=1000)


class ReportDeliveryRescindRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)
