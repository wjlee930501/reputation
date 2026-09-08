"""공개 가시성 — 공개 사이트와 admin이 같은 답을 내는 유일한 판정.

`api/public/site.py`는 이 판정으로 글을 숨기고(fail-closed), admin은 같은 판정으로
"공개 중 / 공개 보류(사유)"를 표시한다. 두 곳이 각자 판정하면 admin은 초록인데 공개
페이지에는 없는 글이 생기고 운영자는 알 방법이 없다(H-01).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Load, load_only

from app.models.content import ContentItem, ContentStatus
from app.services.content_publication import (
    PUBLICATION_CHECK_FIELDS,
    has_required_faq_fields,
    has_required_references,
    image_certification_current,
    public_candidate_review_safe,
    publication_field_values,
)
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED
from app.services.essence_readiness import get_public_approved_philosophy_ids
from app.utils.medical_filter import check_forbidden_content_fields

UNSET_PHILOSOPHY = object()

# 글이 아니라 병원 게이트가 막는 경우. `assess_public_visibility`는 글 하나만 판정하므로
# 이 코드는 admin 직렬화가 `is_public_serving_hospital`을 보고 앞에 붙인다. 공개 사이트는
# 이 코드를 만들 일이 없다 — 병원 게이트가 글 판정보다 먼저 404를 내기 때문이다.
HOSPITAL_NOT_SERVING: Final = "HOSPITAL_NOT_SERVING"

VISIBILITY_BLOCKER_LABELS: dict[str, str] = {
    HOSPITAL_NOT_SERVING: "병원 공개 서비스 중이 아님",
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


# `assess_public_visibility`와 그 헬퍼가 실제로 읽는 컬럼. 판정 표본은 행 단위로 읽어야
# 하므로, 판정에 쓰지 않는 대용량 컬럼(content_brief·image_prompt·검수 이력)까지 실어
# 나르지 않는다. body는 공백·금지 표현 검사가 쓰므로 뺄 수 없다. 판정에 새 필드를 더하면
# 이 목록에도 더해야 한다 — 빠뜨리면 지연 로딩이 async 세션에서 바로 드러난다.
_VISIBILITY_COLUMNS: Final = (
    ContentItem.id,
    ContentItem.hospital_id,
    ContentItem.status,
    ContentItem.content_type,
    ContentItem.title,
    ContentItem.body,
    ContentItem.meta_description,
    ContentItem.published_at,
    ContentItem.essence_status,
    ContentItem.essence_check_summary,
    ContentItem.content_philosophy_id,
    ContentItem.faq_question,
    ContentItem.faq_answer_summary,
    ContentItem.references_list,
    ContentItem.image_url,
    ContentItem.image_policy_verified_at,
    ContentItem.image_content_hash,
    ContentItem.image_subject_hash,
    ContentItem.image_policy_version,
)


def visibility_load_only() -> Load:
    """공개 가시성 판정에 필요한 컬럼만 싣는 로더 옵션."""
    return load_only(*_VISIBILITY_COLUMNS)


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


def withheld_by_hospital_gate(visibility: PublicVisibility) -> PublicVisibility:
    """병원이 공개 서비스 중이 아닐 때의 판정 — 글 자체의 사유는 그대로 뒤에 남긴다.

    admin 전용이다. 글은 완벽해도 사이트가 그 병원의 어떤 글도 내보내지 않으므로,
    "공개 중"이라고 말하면 AE는 없는 페이지를 고객에게 알린다(H-01과 같은 종류의 갈라짐).
    """

    return PublicVisibility(
        visible=False, blockers=(HOSPITAL_NOT_SERVING, *visibility.blockers)
    )


async def assess_sampled_visibility(
    db: AsyncSession,
    items: Iterable[Any],
) -> dict[uuid.UUID, PublicVisibility]:
    """표본 행별 공개 가시성 — 승인 기준 조회는 병원 수와 무관하게 쿼리 2회다.

    운영 큐가 각자 자기만의 SQL 조건으로 "공개 중"을 정의하면 화면마다 답이 갈라진다.
    판정 자체는 행마다 하지만, 기준 조회를 묶어 병원 수에 비례하지 않게 한다.
    """
    sampled = list(items)
    philosophy_ids = await get_public_approved_philosophy_ids(
        db, [item.hospital_id for item in sampled]
    )
    return {
        item.id: assess_public_visibility(item, philosophy_ids[item.hospital_id])
        for item in sampled
    }
