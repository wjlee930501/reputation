"""Baseline-honest monthly mention attribution from frozen manifest cells."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol, TypedDict, assert_never
from uuid import UUID

from app.models.content import ContentType
from app.services.content_citations import build_citation_match, hub_page_label
from app.services.monthly_sov_types import ManifestCellInput

CONTENT_TYPE_ORDER: Final = (
    "FAQ", "DISEASE", "TREATMENT", "COLUMN", "HEALTH", "LOCAL", "NOTICE"
)


class AttributionContent(Protocol):
    content_type: str | ContentType
    title: str | None
    query_target_id: UUID | None


class MentionCellPayload(TypedDict):
    classification: Literal[
        "NEW_MENTION", "FIRST_MEASURED_MENTION", "NON_COMPARABLE", "LOST_MENTION"
    ]
    query_text: str
    platform_label: str
    classification_label: str
    meaning: str
    customer_impact: str
    next_action: str
    related_contents: list[str]


class QuestionRowPayload(TypedDict):
    """질문 1개(플랫폼 합산)의 지난달·이번달 언급 횟수. 원장 리포트 2쪽 부록의 행."""

    query_key: str
    query_text: str
    current_attempts_used: int
    current_mentioned_attempts: int
    prior_attempts_used: int
    prior_mentioned_attempts: int
    prior_measured: bool


class ContentAttributionPayload(TypedDict):
    content_type_counts: dict[str, int]
    prev_content_type_counts: dict[str, int]
    published_count: int
    prev_published_count: int
    new_mention_cells: list[MentionCellPayload]
    first_measured_mention_cells: list[MentionCellPayload]
    non_comparable_cells: list[MentionCellPayload]
    lost_mention_cells: list[MentionCellPayload]
    new_mention_queries: list[MentionCellPayload]
    new_mention_count: int
    first_measured_mention_count: int
    non_comparable_count: int
    lost_mention_count: int
    # 지난달 manifest가 아예 없으면 "빠진 질문"을 말할 근거 자체가 없다.
    has_prior_month: bool
    question_rows: list[QuestionRowPayload]
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
    classification: Literal[
        "NEW_MENTION", "FIRST_MEASURED_MENTION", "NON_COMPARABLE", "LOST_MENTION"
    ]
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


LOST_MENTION_COPY: Final = AttributionCopy(
    "LOST_MENTION",
    "이번 달 빠진 언급",
    "지난달에는 같은 질문에서 병원이 나왔는데 이번 달에는 한 번도 나오지 않았습니다.",
    "성과가 나빠졌다고 단정할 수는 없지만 다음 달에 먼저 확인할 질문입니다.",
    "이 질문을 다음 달 콘텐츠 주제 후보로 올리세요.",
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
    lost_mentions: list[MentionCellPayload] = []
    for cell in sorted(request.current_cells, key=lambda row: (row.query_key, row.platform)):
        prior_cell = prior_by_key.get((cell.query_key, cell.platform))
        if _is_lost_mention(cell, prior_cell):
            lost_mentions.append(_payload(
                cell,
                request.published_contents,
                LOST_MENTION_COPY,
            ))
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
        "lost_mention_cells": lost_mentions[:visible],
        "new_mention_queries": new_mentions[:visible],
        "new_mention_count": len(new_mentions),
        "first_measured_mention_count": len(first_measured),
        "non_comparable_count": len(non_comparable),
        "lost_mention_count": len(lost_mentions),
        "has_prior_month": request.prior_cells is not None,
        "question_rows": _question_rows(request.current_cells, prior_by_key),
        "sov_pct": request.sov_pct,
        "prev_sov_pct": request.prev_sov_pct,
        "change_pct": request.change_pct,
    }


def _is_lost_mention(
    cell: ManifestCellInput, prior: ManifestCellInput | None
) -> bool:
    """지난달 이 셀에서는 나왔고(k>0) 이번 달에는 한 번도 안 나온(k=0) 경우.

    비교의 분모는 헤드라인과 같은 **매칭 코호트**다 — 두 달 모두 성공 반복이
    있어야 하고, 없는 쪽은 "빠졌다"가 아니라 "측정이 없다"이므로 제외한다.
    새 언급(`selected_attempt` 기준)과 달리 여기서는 셀 빈도(k/n)를 쓴다:
    "이번 달에 한 번도 안 나왔다"는 대표 1건이 아니라 반복 전체가 답해야 한다.
    """
    if prior is None:
        return False
    return (
        prior.attempts_used > 0
        and prior.mentioned_attempts > 0
        and cell.attempts_used > 0
        and cell.mentioned_attempts == 0
    )


def _question_rows(
    current_cells: tuple[ManifestCellInput, ...],
    prior_by_key: Mapping[tuple[str, str], ManifestCellInput],
) -> list[QuestionRowPayload]:
    """질문 단위(플랫폼 합산) 지난달·이번달 표. 원장 리포트 2쪽 부록이 읽는다."""
    rows: dict[str, QuestionRowPayload] = {}
    for cell in sorted(current_cells, key=lambda row: (row.query_key, row.platform)):
        row = rows.get(cell.query_key)
        if row is None:
            row = {
                "query_key": cell.query_key,
                "query_text": cell.query_text,
                "current_attempts_used": 0,
                "current_mentioned_attempts": 0,
                "prior_attempts_used": 0,
                "prior_mentioned_attempts": 0,
                "prior_measured": False,
            }
            rows[cell.query_key] = row
        row["current_attempts_used"] += cell.attempts_used
        row["current_mentioned_attempts"] += cell.mentioned_attempts
        prior = prior_by_key.get((cell.query_key, cell.platform))
        if prior is not None:
            row["prior_attempts_used"] += prior.attempts_used
            row["prior_mentioned_attempts"] += prior.mentioned_attempts
            row["prior_measured"] = row["prior_measured"] or prior.attempts_used > 0
    return list(rows.values())


# ══════════════════════════════════════════════════════════════════
# 인용 귀속 — "AI 답변이 우리 글을 실제로 읽었는가"
#
# 언급률은 인용률의 함수다(indexnow.py:3-10 실측: 인용 시 93% vs 미인용 4%).
# 여기서는 인과를 주장하지 않고, 같은 셀(질문×AI 서비스)의 채택 답변이 인용한
# URL을 병원 공개 표면에 귀속해 **관찰된 사실**만 집계한다.
# ══════════════════════════════════════════════════════════════════


class CitedQueryPayload(TypedDict):
    query_text: str
    platform_label: str


class CitedContentPayload(TypedDict):
    content_id: str
    title: str | None
    content_type: str | None
    cited_cell_count: int
    cited_url_count: int
    queries: list[CitedQueryPayload]


class CitedHubPagePayload(TypedDict):
    page_key: str
    label: str
    cited_cell_count: int
    queries: list[CitedQueryPayload]


class CitationSummaryPayload(TypedDict):
    measured_cell_count: int
    cited_cell_count: int
    cited_cell_pct: float | None
    content_cited_cell_count: int
    hub_cited_cell_count: int
    cited_content_count: int
    cited_items: list[CitedContentPayload]
    hub_pages: list[CitedHubPagePayload]


@dataclass(frozen=True, slots=True)
class CitationAttributionInput:
    """`hospital`은 slug·aeo_domain만 읽는다 (ORM 인스턴스가 아니어도 된다)."""

    hospital: Any
    cells: tuple[ManifestCellInput, ...]
    records_by_id: Mapping[UUID, Any] = field(default_factory=dict)
    content_items: Sequence[Any] = ()
    max_visible_items: int = 10


def _content_type_code(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if isinstance(value, ContentType) else str(value)


def _source_urls_of(record: Any) -> list[str]:
    return [
        value.strip()
        for value in (getattr(record, "source_urls", None) or [])
        if isinstance(value, str) and value.strip()
    ]


def build_citation_attribution(
    request: CitationAttributionInput,
) -> CitationSummaryPayload:
    """월간 manifest 셀의 성공 답변들이 인용한 자사 URL을 글 단위로 집계한다.

    헤드라인(월간 언급 빈도)과 같은 원칙으로 셀의 **모든 성공 반복**을 본다 — 반복
    중 하나라도 자사 URL을 인용했으면 그 셀은 "인용됨"이고, 글·허브 페이지 매칭은
    반복 간 합집합이다. 대표 1건만 보면 인용이 구조적으로 과소 집계된다.
    """
    content_by_key = {
        str(getattr(item, "id", "")).strip().lower(): item
        for item in request.content_items
        if getattr(item, "id", None) is not None
    }
    measured_cell_count = 0
    cited_cell_count = 0
    content_cited_cell_count = 0
    hub_cited_cell_count = 0
    content_cells: dict[str, int] = {}
    content_urls: dict[str, set[str]] = {}
    content_queries: dict[str, list[CitedQueryPayload]] = {}
    hub_cells: dict[str, int] = {}
    hub_queries: dict[str, list[CitedQueryPayload]] = {}

    for cell in sorted(request.cells, key=lambda row: (row.query_key, row.platform)):
        # 헤드라인과 **같은 접근자**를 쓴다. `cell.attempts`를 직접 거르면
        # state=FAILED인데 성공 시도가 남아 있는 손상 셀이 여기서만 측정으로
        # 세어져, 인용 분모(확인한 답변 수)가 헤드라인 분모와 어긋난다.
        succeeded = cell.successful_attempts
        if not succeeded:
            continue
        measured_cell_count += 1
        cell_urls: list[str] = []
        for attempt in succeeded:
            record = request.records_by_id.get(attempt.record_id)
            if record is not None:
                cell_urls.extend(_source_urls_of(record))
        if not cell_urls:
            continue
        match = build_citation_match(cell_urls, request.hospital, request.content_items)
        if not match.has_owned:
            continue
        cited_cell_count += 1
        query: CitedQueryPayload = {
            "query_text": cell.query_text,
            "platform_label": _platform_label(cell.platform),
        }
        if match.contents:
            content_cited_cell_count += 1
        if match.hub_pages:
            hub_cited_cell_count += 1
        for content_key, urls in match.contents.items():
            content_cells[content_key] = content_cells.get(content_key, 0) + 1
            content_urls.setdefault(content_key, set()).update(urls)
            rows = content_queries.setdefault(content_key, [])
            if query not in rows:
                rows.append(query)
        for page_key in match.hub_pages:
            hub_cells[page_key] = hub_cells.get(page_key, 0) + 1
            hub_rows_for_page = hub_queries.setdefault(page_key, [])
            if query not in hub_rows_for_page:
                hub_rows_for_page.append(query)

    cited_items: list[CitedContentPayload] = [
        {
            "content_id": key,
            "title": getattr(content_by_key.get(key), "title", None),
            "content_type": _content_type_code(
                getattr(content_by_key.get(key), "content_type", None)
            ),
            "cited_cell_count": count,
            "cited_url_count": len(content_urls.get(key, ())),
            "queries": content_queries.get(key, []),
        }
        for key, count in content_cells.items()
    ]
    cited_items.sort(
        key=lambda row: (-row["cited_cell_count"], str(row["title"] or ""), row["content_id"])
    )
    hub_pages: list[CitedHubPagePayload] = [
        {
            "page_key": key,
            "label": hub_page_label(key),
            "cited_cell_count": count,
            "queries": hub_queries.get(key, []),
        }
        for key, count in hub_cells.items()
    ]
    hub_pages.sort(key=lambda row: (-row["cited_cell_count"], row["page_key"]))

    return {
        "measured_cell_count": measured_cell_count,
        "cited_cell_count": cited_cell_count,
        "cited_cell_pct": (
            round(cited_cell_count / measured_cell_count * 100, 1)
            if measured_cell_count
            else None
        ),
        "content_cited_cell_count": content_cited_cell_count,
        "hub_cited_cell_count": hub_cited_cell_count,
        "cited_content_count": len(cited_items),
        "cited_items": cited_items[: request.max_visible_items],
        "hub_pages": hub_pages[: request.max_visible_items],
    }
