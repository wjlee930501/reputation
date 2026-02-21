from typing import Optional

from pydantic import BaseModel


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


class ContentItemDetail(ContentItemResponse):
    body: Optional[str]
    image_prompt: Optional[str]
