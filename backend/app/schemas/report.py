from typing import Any, Optional

from pydantic import BaseModel


class ReportResponse(BaseModel):
    id: str
    hospital_id: str
    period_year: int
    period_month: int
    report_type: str
    pdf_path: Optional[str]
    sov_summary: Optional[Any]
    content_summary: Optional[Any]
    created_at: str
    sent_at: Optional[str]
