from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ReportResponse(BaseModel):
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


class ReportDeliveryRequest(BaseModel):
    artifact_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    recipient_label: str = Field(min_length=1, max_length=255)
    channel: str = Field(min_length=1, max_length=100)
    note: Optional[str] = Field(default=None, max_length=1000)


class ReportDeliveryCorrectionRequest(ReportDeliveryRequest):
    reason: str = Field(min_length=2, max_length=1000)


class ReportDeliveryRescindRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)
