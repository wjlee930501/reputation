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
    status: str
    generated_at: Optional[str]
    published_at: Optional[str]
    published_by: Optional[str]
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
