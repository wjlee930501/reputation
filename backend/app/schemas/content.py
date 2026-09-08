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
    post_publish_notified_at: Optional[str] = None
    post_publish_reviewed_at: Optional[str] = None
    post_publish_reviewed_by: Optional[str] = None
    # 사람이 확인해야 하는 표본인지 (post_publish_review_policy). Admin은 이 값이 True일
    # 때만 "문제 없음 · 공개 내용 확인 완료" 버튼을 띄운다.
    post_publish_review_required: bool = False
    body_updated_at: Optional[str] = None
    # 참고 자료/FAQ 분리 필드 — Admin 검수·보정(A1)과 컴플라이언스 패널이 사용.
    references: list[dict[str, Any]] = []
    faq_question: Optional[str] = None
    faq_answer_summary: Optional[str] = None
    compliance: Optional[dict[str, Any]] = None
    # 월 표의 행 상태 — kind/label/reason/link. 공개 사이트와 같은 판정 위에 차단 링크만
    # 얹은 값이며, Admin은 라벨을 새로 만들지 않는다. 필수 — 빠진 응답이 있으면
    # 그 화면만 상태를 자기 방식으로 다시 계산하게 된다.
    row_state: dict[str, Any]
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
