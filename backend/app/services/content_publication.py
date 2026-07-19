"""Single publication policy shared by manual recovery and scheduled auto-publish."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models.content import ContentItem
from app.models.essence import HospitalContentPhilosophy
from app.services.content_engine import FORBIDDEN_CHECK_FIELDS, forbidden_check_text
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    screen_content_against_philosophy,
)
from app.utils.authority_sources import is_citable_reference_url
from app.utils.medical_filter import check_forbidden


@dataclass(frozen=True)
class PublicationAssessment:
    publishable: bool
    code: str | None
    message: str | None
    violations: tuple[str, ...]
    essence_status: str
    essence_summary: dict[str, Any]
    philosophy_id: object | None


def has_required_references(item: ContentItem) -> bool:
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

    violations = tuple(dict.fromkeys(check_forbidden(publication_text(item))))
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
