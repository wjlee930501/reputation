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
    page_count: Literal[1] | None = None
    validated_at: datetime | None = None
    validation_version: Literal["doctor-pdf-v1"] | None = None


class ReportListResponse(BaseModel):
    id: str
    hospital_id: str
    period_year: int
    period_month: int
    report_type: str
    display: Optional[dict[str, Any]] = None
    has_pdf: bool
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
    delivery_blockers: list[str] = Field(default_factory=list)
    effective_delivery: Optional[dict[str, Any]] = None
    delivery_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    sent_at: Optional[str]


class ReportResponse(ReportListResponse):
    doctor_artifact: Optional[DoctorArtifactProjection] = None


class ReportDeliveryRequest(BaseModel):
    artifact_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    recipient_label: str = Field(min_length=1, max_length=255)
    channel: str = Field(min_length=1, max_length=100)
    note: Optional[str] = Field(default=None, max_length=1000)


class ReportDeliveryCorrectionRequest(ReportDeliveryRequest):
    reason: str = Field(min_length=2, max_length=1000)


class ReportDeliveryRescindRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)
