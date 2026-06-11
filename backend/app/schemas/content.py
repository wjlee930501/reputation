import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

ContentBriefStatus = Literal["DRAFT", "APPROVED", "NEEDS_REVIEW"]


class ContentItemResponse(BaseModel):
    id: str
    content_type: str
    sequence_no: int
    total_count: int
    title: Optional[str]
    meta_description: Optional[str]
    image_url: Optional[str]
    scheduled_date: str
    # 전월 이월 기준일 (월말 반려 carry-over) — 원래 발행 예정일. 내부 운영 데이터로
    # Admin 응답에만 포함하고 공개(/site) 직렬화에는 노출하지 않는다.
    carried_over_from: Optional[str] = None
    status: str
    display: Optional[dict[str, Any]] = None
    generated_at: Optional[str]
    published_at: Optional[str]
    published_by: Optional[str]
    body_updated_at: Optional[str] = None
    # 참고 자료/FAQ 분리 필드 — Admin 검수·보정(A1)과 컴플라이언스 패널이 사용.
    references: list[dict[str, Any]] = []
    faq_question: Optional[str] = None
    faq_answer_summary: Optional[str] = None
    compliance: Optional[dict[str, Any]] = None
    content_philosophy_id: Optional[str] = None
    query_target_id: Optional[str] = None
    exposure_action_id: Optional[str] = None
    content_brief: Optional[dict[str, Any]] = None
    brief_status: Optional[str] = None
    brief_approved_at: Optional[str] = None
    brief_approved_by: Optional[str] = None
    essence_status: Optional[str] = None
    essence_check_summary: Optional[dict] = None


class ContentItemDetail(ContentItemResponse):
    body: Optional[str]
    image_prompt: Optional[str]


class ContentBriefUpdate(BaseModel):
    query_target_id: uuid.UUID | None = None
    exposure_action_id: uuid.UUID | None = None
    content_brief: dict[str, Any] | None = None
    brief_status: ContentBriefStatus | None = None
    brief_approved_by: str | None = Field(default=None, max_length=100)
    regenerate_brief: bool = False
