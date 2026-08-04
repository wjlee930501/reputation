from typing import Any, Optional

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
    download_url: Optional[str] = None
    sov_summary: Optional[Any]
    content_summary: Optional[Any]
    essence_summary: Optional[Any] = None
    delivery_ready: bool = False
    delivery_blockers: list[str] = Field(default_factory=list)
    created_at: str
    sent_at: Optional[str]
