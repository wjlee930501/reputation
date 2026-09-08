"""공개 가시성 — 공개 사이트와 admin이 같은 답을 내는 유일한 판정.

`api/public/site.py`는 이 판정으로 글을 숨기고(fail-closed), admin은 같은 판정으로
"공개 중 / 공개 보류(사유)"를 표시한다. 두 곳이 각자 판정하면 admin은 초록인데 공개
페이지에는 없는 글이 생기고 운영자는 알 방법이 없다(H-01).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.models.content import ContentStatus
from app.services.content_publication import (
    PUBLICATION_CHECK_FIELDS,
    has_required_faq_fields,
    has_required_references,
    image_certification_current,
    public_candidate_review_safe,
    publication_field_values,
)
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED
from app.utils.medical_filter import check_forbidden_content_fields

UNSET_PHILOSOPHY = object()

VISIBILITY_BLOCKER_LABELS: dict[str, str] = {
    "PHILOSOPHY_MISMATCH": "현재 승인된 콘텐츠 운영 기준과 다른 기준으로 생성됨",
    "STATUS_NOT_PUBLISHED": "발행 상태가 아님",
    "ESSENCE_NOT_ALIGNED": "콘텐츠 운영 기준 재검토 필요",
    "EMPTY_TITLE": "제목이 비어 있음",
    "EMPTY_BODY": "본문이 비어 있음",
    "NOT_PUBLISHED_AT": "발행 시각이 없음",
    "FAQ_FIELDS_MISSING": "FAQ 질문·직접 답변 누락",
    "MISSING_REFERENCES": "인용 가능한 참고 자료 없음",
    "IMAGE_NOT_CERTIFIED": "대표 이미지 재인증 대기",
    "AI_REVIEW_UNRESOLVED": "독립 검수 지적 미해결",
    "FORBIDDEN_EXPRESSION": "의료광고 금지 표현 포함",
}


@dataclass(frozen=True, slots=True)
class PublicVisibility:
    visible: bool
    blockers: tuple[str, ...]

    @property
    def blocker_labels(self) -> list[str]:
        return [VISIBILITY_BLOCKER_LABELS.get(code, code) for code in self.blockers]


def assess_public_visibility(
    item: Any,
    current_philosophy_id: uuid.UUID | None | object = UNSET_PHILOSOPHY,
) -> PublicVisibility:
    """저장된 그대로의 글이 지금 공개 페이지에 나가도 되는가. 모든 차단 사유를 모은다."""
    blockers: list[str] = []
    if current_philosophy_id is not UNSET_PHILOSOPHY and not (
        current_philosophy_id is not None
        and getattr(item, "content_philosophy_id", None) == current_philosophy_id
    ):
        blockers.append("PHILOSOPHY_MISMATCH")
    status = getattr(item, "status", None)
    if getattr(status, "value", status) != ContentStatus.PUBLISHED.value:
        blockers.append("STATUS_NOT_PUBLISHED")
    if getattr(item, "essence_status", None) != ESSENCE_STATUS_ALIGNED:
        blockers.append("ESSENCE_NOT_ALIGNED")
    if not (getattr(item, "title", None) or "").strip():
        blockers.append("EMPTY_TITLE")
    if not (getattr(item, "body", None) or "").strip():
        blockers.append("EMPTY_BODY")
    if getattr(item, "published_at", None) is None:
        blockers.append("NOT_PUBLISHED_AT")
    if not has_required_faq_fields(item):
        blockers.append("FAQ_FIELDS_MISSING")
    if not has_required_references(item):
        blockers.append("MISSING_REFERENCES")
    if not image_certification_current(item):
        blockers.append("IMAGE_NOT_CERTIFIED")
    if not public_candidate_review_safe(item):
        blockers.append("AI_REVIEW_UNRESOLVED")
    if check_forbidden_content_fields(publication_field_values(item), PUBLICATION_CHECK_FIELDS):
        blockers.append("FORBIDDEN_EXPRESSION")
    return PublicVisibility(visible=not blockers, blockers=tuple(blockers))
