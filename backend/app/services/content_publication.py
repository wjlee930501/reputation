"""Single publication policy shared by manual recovery and scheduled auto-publish."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models.content import ContentItem
from app.models.essence import HospitalContentPhilosophy
from app.services.content_engine import (
    FORBIDDEN_CHECK_FIELDS,
    REFERENCES_REQUIRED_TYPES,
    forbidden_check_text,
)
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    screen_content_against_philosophy,
)
from app.utils.authority_sources import is_citable_reference_url
from app.utils.medical_filter import check_forbidden_content_fields


@dataclass(frozen=True)
class PublicationAssessment:
    publishable: bool
    code: str | None
    message: str | None
    violations: tuple[str, ...]
    essence_status: str
    essence_summary: dict[str, Any]
    philosophy_id: object | None


def _type_value(content_type: object) -> str:
    """ContentType enum / 문자열 / value 속성을 가진 객체를 공통 문자열로 정규화."""
    return str(getattr(content_type, "value", content_type) or "").upper()


# 유형 비교는 값 문자열로 한다 — 호출부가 enum을 넘길 수도, 직렬화된 문자열을 넘길 수도 있다.
_REFERENCES_REQUIRED_VALUES = frozenset(_type_value(t) for t in REFERENCES_REQUIRED_TYPES)


def has_required_references(item: ContentItem) -> bool:
    """이 항목에 참고 자료가 필수인가.

    생성 검증(content_engine)과 **같은 유형 집합**을 쓴다. NOTICE는 순수 운영 공지라
    생성 단계에서 참고 자료를 요구하지 않는데, 발행 게이트만 유형 구분 없이 요구하면
    NOTICE는 생성은 되고 발행은 매일 MISSING_REFERENCES로 막히다가 조회 대상에서
    빠져 영구 DRAFT로 사망한다.
    """
    content_type = getattr(item, "content_type", None)
    # 유형을 못 읽으면 요구하는 쪽(fail-safe)으로 둔다 — 근거 없는 의료 콘텐츠가
    # 유형 판정 실패만으로 공개되면 안 된다.
    if content_type is not None and _type_value(content_type) not in _REFERENCES_REQUIRED_VALUES:
        return True
    return count_citable_references(item) > 0


def count_citable_references(item: ContentItem) -> int:
    references = item.references_list or []
    return sum(
        1
        for ref in references
        if isinstance(ref, dict)
        and str(ref.get("title") or "").strip()
        and is_citable_reference_url(str(ref.get("url") or "").strip())
    )


# 참고 자료 제목도 공개 표면에 그대로 렌더되고(콘텐츠 상세의 "참고 자료" 섹션)
# JSON-LD citation.name으로도 나간다. 제목은 모델 자유 출력인데 URL만 화이트리스트
# 검증을 거치고 제목은 길이 절단만 됐다 — 금지 표현 검사기가 한 번도 본 적이 없었다.
# 마크다운이 아니라 리터럴 렌더이므로 평문 기준으로 검사한다.
REFERENCE_TITLES_FIELD = "reference_titles"
PUBLICATION_CHECK_FIELDS = (*FORBIDDEN_CHECK_FIELDS, REFERENCE_TITLES_FIELD)


def publication_field_values(item: ContentItem) -> dict:
    values = {field: getattr(item, field, None) for field in FORBIDDEN_CHECK_FIELDS}
    values[REFERENCE_TITLES_FIELD] = " ".join(
        str(ref.get("title") or "").strip()
        for ref in (item.references_list or [])
        if isinstance(ref, dict)
    ).strip()
    return values


def publication_text(item: ContentItem) -> str:
    return forbidden_check_text(
        {field: getattr(item, field, None) for field in FORBIDDEN_CHECK_FIELDS}
    )


def assess_content_publication(
    item: ContentItem,
    philosophy: HospitalContentPhilosophy | None,
) -> PublicationAssessment:
    """Re-screen the exact stored content immediately before it becomes public."""

    if not item.title or not item.body:
        return _blocked(
            code="CONTENT_NOT_GENERATED",
            message="제목과 본문이 아직 생성되지 않았습니다.",
            item=item,
            philosophy=philosophy,
        )
    if not has_required_references(item):
        return _blocked(
            code="MISSING_REFERENCES",
            message="권위 있는 참고 자료가 1개 이상 필요합니다.",
            item=item,
            philosophy=philosophy,
        )

    # 필드별로 올바른 기준을 적용한다 — 본문은 마크다운 렌더 결과 기준, 제목·메타·FAQ는
    # 평문 기준. 합쳐서 한 번에 검사하면 `최**고**의`가 통과하거나(본문 우회) 제목의
    # 리터럴 별표가 위반으로 오탐되는 등 양방향으로 틀린다.
    violations = tuple(
        check_forbidden_content_fields(publication_field_values(item), PUBLICATION_CHECK_FIELDS)
    )
    if violations:
        summary = {
            "blocking": True,
            "findings": [f"의료광고 금지 표현: {', '.join(violations)}"],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        return PublicationAssessment(
            publishable=False,
            code="FORBIDDEN_EXPRESSION",
            message="의료광고 금지 표현이 포함되어 있어 발행할 수 없습니다.",
            violations=violations,
            essence_status=ESSENCE_STATUS_NEEDS_REVIEW,
            essence_summary=summary,
            philosophy_id=getattr(philosophy, "id", None),
        )

    screening = screen_content_against_philosophy(item, philosophy)
    if screening.status != ESSENCE_STATUS_ALIGNED:
        return PublicationAssessment(
            publishable=False,
            code="ESSENCE_NOT_ALIGNED",
            message="최신 승인 콘텐츠 운영 기준의 자동 검사를 통과하지 못했습니다.",
            violations=(),
            essence_status=screening.status,
            essence_summary=screening.summary,
            philosophy_id=getattr(philosophy, "id", None),
        )

    return PublicationAssessment(
        publishable=True,
        code=None,
        message=None,
        violations=(),
        essence_status=screening.status,
        essence_summary=screening.summary,
        philosophy_id=getattr(philosophy, "id", None),
    )


def apply_publication_assessment(item: ContentItem, assessment: PublicationAssessment) -> None:
    item.content_philosophy_id = assessment.philosophy_id
    item.essence_status = assessment.essence_status
    item.essence_check_summary = assessment.essence_summary


def _blocked(
    *,
    code: str,
    message: str,
    item: ContentItem,
    philosophy: HospitalContentPhilosophy | None,
) -> PublicationAssessment:
    screening = screen_content_against_philosophy(item, philosophy)
    summary = dict(screening.summary or {})
    findings = list(summary.get("findings") or [])
    if message not in findings:
        findings.append(message)
    summary.update(
        {
            "blocking": True,
            "findings": findings,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return PublicationAssessment(
        publishable=False,
        code=code,
        message=message,
        violations=(),
        essence_status=screening.status,
        essence_summary=summary,
        philosophy_id=getattr(philosophy, "id", None),
    )
