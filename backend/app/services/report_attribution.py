"""Baseline-honest monthly mention attribution from frozen manifest cells."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypedDict, assert_never
from uuid import UUID

from app.models.content import ContentType
from app.services.monthly_sov_types import ManifestCellInput

CONTENT_TYPE_ORDER: Final = (
    "FAQ", "DISEASE", "TREATMENT", "COLUMN", "HEALTH", "LOCAL", "NOTICE"
)


class AttributionContent(Protocol):
    content_type: str | ContentType
    title: str | None
    query_target_id: UUID | None


class MentionCellPayload(TypedDict):
    classification: Literal["NEW_MENTION", "FIRST_MEASURED_MENTION", "NON_COMPARABLE"]
    query_text: str
    platform_label: str
    classification_label: str
    meaning: str
    customer_impact: str
    next_action: str
    related_contents: list[str]


class ContentAttributionPayload(TypedDict):
    content_type_counts: dict[str, int]
    prev_content_type_counts: dict[str, int]
    published_count: int
    prev_published_count: int
    new_mention_cells: list[MentionCellPayload]
    first_measured_mention_cells: list[MentionCellPayload]
    non_comparable_cells: list[MentionCellPayload]
    new_mention_queries: list[MentionCellPayload]
    new_mention_count: int
    first_measured_mention_count: int
    non_comparable_count: int
    sov_pct: float | None
    prev_sov_pct: float | None
    change_pct: float | None


@dataclass(frozen=True, slots=True)
class ContentAttributionInput:
    published_contents: Sequence[AttributionContent]
    prev_published_contents: Sequence[AttributionContent]
    current_cells: tuple[ManifestCellInput, ...]
    prior_cells: tuple[ManifestCellInput, ...] | None
    sov_pct: float | None
    prev_sov_pct: float | None
    change_pct: float | None
    max_visible_cells: int = 5


@dataclass(frozen=True, slots=True)
class AttributionCopy:
    classification: Literal["NEW_MENTION", "FIRST_MEASURED_MENTION", "NON_COMPARABLE"]
    label: str
    meaning: str
    customer_impact: str
    next_action: str


FIRST_MEASURED_COPY: Final = AttributionCopy(
    "FIRST_MEASURED_MENTION",
    "이번 달 처음 확인된 언급",
    "지난달 같은 질문의 측정 기록이 없어 새 언급으로 계산하지 않습니다.",
    "지난달보다 좋아진 결과로 설명할 수 없습니다.",
    "이번 달 현재 상태로만 전달하고 다음 달 같은 질문 결과와 비교하세요.",
)
NON_COMPARABLE_COPY: Final = AttributionCopy(
    "NON_COMPARABLE",
    "지난달과 비교할 수 없는 언급",
    "지난달 같은 질문의 측정이 완료되지 않았습니다.",
    "새로 좋아진 결과로 설명할 수 없어 새 언급 수에서 제외했습니다.",
    "이번 결과는 현재 상태로만 전달하고 다음 달 정상 측정 후 비교하세요.",
)
NEW_MENTION_COPY: Final = AttributionCopy(
    "NEW_MENTION",
    "지난달보다 새로 확인된 언급",
    "지난달에도 정상 측정된 같은 질문에서는 병원이 나오지 않았고 이번 달에는 나왔습니다.",
    "같은 질문과 AI 서비스 기준으로 확인된 변화입니다.",
    "인과관계로 단정하지 말고 같은 기간에 관찰된 변화로 설명하세요.",
)


def _content_type_counts(contents: Sequence[AttributionContent]) -> dict[str, int]:
    counts = dict.fromkeys(CONTENT_TYPE_ORDER, 0)
    for content in contents:
        value = content.content_type
        match value:
            case ContentType():
                code = value.value
            case str():
                code = value
            case unreachable:
                assert_never(unreachable)
        if code in counts:
            counts[code] += 1
    return counts


def _related_content_titles(
    cell: ManifestCellInput, contents: Sequence[AttributionContent]
) -> list[str]:
    linked = [
        content.title
        for content in contents
        if cell.query_target_id is not None
        and content.query_target_id == cell.query_target_id
        and content.title
    ]
    if linked:
        return list(dict.fromkeys(linked))
    tokens = [token for token in re.split(r"\s+", cell.query_text) if len(token) >= 2]
    matched = [
        content.title
        for content in contents
        if content.title and any(token in content.title for token in tokens)
    ]
    return list(dict.fromkeys(matched))


def _platform_label(platform: str) -> str:
    return {"chatgpt": "ChatGPT", "gemini": "Gemini"}.get(platform.lower(), "기타 AI 서비스")


def _payload(
    cell: ManifestCellInput,
    contents: Sequence[AttributionContent],
    copy: AttributionCopy,
) -> MentionCellPayload:
    return {
        "classification": copy.classification,
        "query_text": cell.query_text,
        "platform_label": _platform_label(cell.platform),
        "classification_label": copy.label,
        "meaning": copy.meaning,
        "customer_impact": copy.customer_impact,
        "next_action": copy.next_action,
        "related_contents": _related_content_titles(cell, contents),
    }


def build_content_attribution_summary(
    request: ContentAttributionInput,
) -> ContentAttributionPayload:
    """Classify current mentions only against the same frozen prior cell.

    셀 하나의 판정은 대표 응답(``selected_attempt``)이 쓴다. 대표는 언급된 시도를
    먼저 고르는 결정적 규칙이므로, 여기서 "새로 확인된 질문"은 **이번 달 반복 중
    한 번이라도 나왔고 지난달에는 한 번도 안 나온 셀**을 뜻한다. 헤드라인 점수는
    이와 달리 셀 빈도(k/n)를 쓴다 — 둘의 목적이 다르다(하나는 사례 나열, 하나는 비율).
    """
    prior_by_key = (
        {(cell.query_key, cell.platform): cell for cell in request.prior_cells}
        if request.prior_cells is not None
        else {}
    )
    new_mentions: list[MentionCellPayload] = []
    first_measured: list[MentionCellPayload] = []
    non_comparable: list[MentionCellPayload] = []
    for cell in sorted(request.current_cells, key=lambda row: (row.query_key, row.platform)):
        current_attempt = cell.selected_attempt
        if current_attempt is None or not current_attempt.is_mentioned:
            continue
        prior = prior_by_key.get((cell.query_key, cell.platform))
        if prior is None:
            first_measured.append(_payload(
                cell,
                request.published_contents,
                FIRST_MEASURED_COPY,
            ))
            continue
        prior_attempt = prior.selected_attempt
        if prior_attempt is None:
            non_comparable.append(_payload(
                cell,
                request.published_contents,
                NON_COMPARABLE_COPY,
            ))
            continue
        if not prior_attempt.is_mentioned:
            new_mentions.append(_payload(
                cell,
                request.published_contents,
                NEW_MENTION_COPY,
            ))

    visible = request.max_visible_cells
    return {
        "content_type_counts": _content_type_counts(request.published_contents),
        "prev_content_type_counts": _content_type_counts(request.prev_published_contents),
        "published_count": len(request.published_contents),
        "prev_published_count": len(request.prev_published_contents),
        "new_mention_cells": new_mentions[:visible],
        "first_measured_mention_cells": first_measured[:visible],
        "non_comparable_cells": non_comparable[:visible],
        "new_mention_queries": new_mentions[:visible],
        "new_mention_count": len(new_mentions),
        "first_measured_mention_count": len(first_measured),
        "non_comparable_count": len(non_comparable),
        "sov_pct": request.sov_pct,
        "prev_sov_pct": request.prev_sov_pct,
        "change_pct": request.change_pct,
    }
