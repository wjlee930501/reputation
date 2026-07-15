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
    download_url: Optional[str] = None
    sov_summary: Optional[Any]
    content_summary: Optional[Any]
    essence_summary: Optional[Any] = None
    delivery_ready: bool = False
    delivery_blockers: list[str] = Field(default_factory=list)
    created_at: str
    sent_at: Optional[str]
