"""Single publication policy shared by manual recovery and scheduled auto-publish."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models.content import ContentItem
from app.models.essence import HospitalContentPhilosophy
from app.services.content_ai_review import candidate_review_coverage, candidate_sha256
from app.services.content_engine import (
    FORBIDDEN_CHECK_FIELDS,
    REFERENCES_REQUIRED_TYPES,
)
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    ESSENCE_STATUS_MISSING_APPROVED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    screen_content_against_philosophy,
)
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
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


def record_publication_identity(
    item: ContentItem,
    *,
    published_at: datetime,
    published_by: str,
) -> datetime:
    """Record the current edition and preserve the immutable first publication fact."""

    known_published_at = getattr(item, "published_at", None)
    known_published_by = getattr(item, "published_by", None)
    if getattr(item, "first_published_at", None) is None:
        # During a rolling deploy an older publisher may have populated only published_at.
        # Preserve that known earlier fact instead of assigning this deployment's clock time.
        item.first_published_at = known_published_at or published_at
        item.first_published_by = (
            known_published_by if known_published_at is not None else published_by
        )
    item.published_at = published_at
    item.published_by = published_by
    return published_at


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


def has_required_faq_fields(item: ContentItem) -> bool:
    if _type_value(getattr(item, "content_type", None)) != "FAQ":
        return True
    question = str(getattr(item, "faq_question", None) or "").strip()
    answer = str(getattr(item, "faq_answer_summary", None) or "").strip()
    return bool(question.endswith("?") and answer)


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


def _blocking_ai_review_state(item: ContentItem) -> tuple[str, dict[str, Any]] | None:
    """Return CURRENT/STALE for an unresolved review that cannot be discarded."""

    summary = getattr(item, "essence_check_summary", None)
    review = summary.get("ai_review") if isinstance(summary, dict) else None
    if not isinstance(review, dict):
        return None
    review_status = review.get("status")
    if review_status == "UNAVAILABLE":
        return "UNAVAILABLE", review
    if review_status != "REVISE":
        return None
    # v2 explicitly distinguishes soft findings. Legacy REVISE payloads did not,
    # so they are safety-uncertain and require one automatic re-review.
    is_legacy = review.get("schema_version") is None
    if not is_legacy and review.get("blocking") is not True:
        return None
    if review.get("candidate_sha256") != candidate_sha256(item):
        return "STALE", review
    coverage = review.get("coverage")
    if not isinstance(coverage, dict) or coverage != candidate_review_coverage(item):
        return "STALE", review
    return "CURRENT", review


def public_candidate_review_safe(item: ContentItem) -> bool:
    """Public read paths must never expose a known unresolved review."""

    return _blocking_ai_review_state(item) is None


def image_certification_current(item: ContentItem) -> bool:
    """Require the stored URL, exact bytes, subject, and policy to remain bound."""

    if not getattr(item, "image_url", None) or not getattr(
        item, "image_policy_verified_at", None
    ):
        return False
    content_hash = getattr(item, "image_content_hash", None)
    subject_hash = getattr(item, "image_subject_hash", None)
    policy_version = getattr(item, "image_policy_version", None)
    url_hash = image_content_hash_from_url(getattr(item, "image_url", None))
    return bool(
        content_hash
        and url_hash
        and content_hash == url_hash
        and subject_hash
        == image_subject_hash(getattr(item, "content_type", None), getattr(item, "title", None))
        and policy_version == IMAGE_POLICY_VERSION
    )


def assess_content_publication(
    item: ContentItem,
    philosophy: HospitalContentPhilosophy | None,
) -> PublicationAssessment:
    """Re-screen the exact stored content immediately before it becomes public."""

    # 공백만 남은 제목·본문은 생성된 원고가 아니다 (H-09).
    if not (item.title or "").strip() or not (item.body or "").strip():
        return _blocked(
            code="CONTENT_NOT_GENERATED",
            message="제목과 본문이 아직 생성되지 않았습니다.",
            item=item,
            philosophy=philosophy,
        )
    if not has_required_faq_fields(item):
        return _blocked(
            code="FAQ_FIELDS_MISSING",
            message="FAQ 질문과 직접 답변 요약이 필요합니다.",
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

    hard_review_state = _blocking_ai_review_state(item)
    if hard_review_state is not None:
        review_state, hard_review = hard_review_state
        findings = [
            str(finding.get("message") or "").strip()
            for finding in (hard_review.get("findings") or [])
            if isinstance(finding, dict) and str(finding.get("message") or "").strip()
        ]
        code = (
            "CONTENT_AI_REVIEW_STALE"
            if review_state == "STALE"
            else "CONTENT_AI_REVIEW_UNAVAILABLE"
            if review_state == "UNAVAILABLE"
            else "CONTENT_AI_HARD_FINDING"
        )
        return _blocked(
            code=code,
            message=(
                "이전의 미해결 사실·의료 안전 지적 이후 후보가 변경되어 독립 재검수가 필요합니다."
                if review_state == "STALE"
                else "독립 AI 검수를 완료하지 못해 공급자 복구 후 자동 재검수가 필요합니다."
                if review_state == "UNAVAILABLE"
                else (
                    findings[0]
                    if findings
                    else "독립 검수의 사실·의료 안전 지적이 해결되지 않았습니다."
                )
            ),
            item=item,
            philosophy=philosophy,
        )

    screening = screen_content_against_philosophy(item, philosophy)
    if screening.status != ESSENCE_STATUS_ALIGNED:
        return PublicationAssessment(
            publishable=False,
            code=(
                "MISSING_APPROVED_ESSENCE"
                if screening.status == ESSENCE_STATUS_MISSING_APPROVED
                else "ESSENCE_NOT_ALIGNED"
            ),
            message=(
                "승인된 콘텐츠 운영 기준이 없습니다."
                if screening.status == ESSENCE_STATUS_MISSING_APPROVED
                else "최신 승인 콘텐츠 운영 기준의 자동 검사를 통과하지 못했습니다."
            ),
            violations=(),
            essence_status=screening.status,
            essence_summary=screening.summary,
            philosophy_id=getattr(philosophy, "id", None),
        )

    if not getattr(item, "image_url", None):
        return _blocked(
            code="CONTENT_IMAGE_NOT_READY",
            message="대표 이미지가 아직 준비되지 않았습니다.",
            item=item,
            philosophy=philosophy,
        )
    if not image_certification_current(item):
        return _blocked(
            code="CONTENT_IMAGE_NOT_VERIFIED",
            message="대표 이미지의 자동 정책 검사가 아직 완료되지 않았습니다.",
            item=item,
            philosophy=philosophy,
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
    previous_summary = getattr(item, "essence_check_summary", None)
    summary = dict(assessment.essence_summary or {})
    if isinstance(previous_summary, dict):
        # Preserve only advisory provenance. Blocking truth and findings always
        # come from the fresh deterministic publication assessment above.
        for key in (
            "automatic_remediation_attempts",
            "reviewer_driven_rewrites",
            "ai_review",
            # Scheduled generation uses this durable JSON fragment to avoid
            # paying again for the same unchanged body/image failure.
            "generation_attempt",
            "legacy_image_certification",
            # 공개 이미지 재인증 차단 표시는 제목(subject)에 매인 사실이다. 제목을
            # 건드리지 않는 편집이 지우면 sweep이 같은 답을 다시 사러 간다 (H-01).
            "image_recertification",
        ):
            value = previous_summary.get(key)
            if value is not None:
                summary[key] = value
    item.content_philosophy_id = assessment.philosophy_id
    if hasattr(item, "last_reviewed_philosophy_id"):
        item.last_reviewed_philosophy_id = assessment.philosophy_id
    item.essence_status = assessment.essence_status
    item.essence_check_summary = summary


def apply_essence_revalidation(
    item: ContentItem,
    philosophy: HospitalContentPhilosophy,
) -> str:
    """Re-screen Essence fields without erasing independent review provenance."""

    screening = screen_content_against_philosophy(item, philosophy)
    item.content_philosophy_id = philosophy.id
    previous = getattr(item, "essence_check_summary", None)
    summary = dict(screening.summary or {})
    if isinstance(previous, dict):
        for key in (
            "automatic_remediation_attempts",
            "reviewer_driven_rewrites",
            "ai_review",
            "generation_attempt",
            "generation_provenance",
            "legacy_image_certification",
            # 재승인은 제목을 바꾸지 않는다. 재인증 차단 표시를 지우면 안 된다 (H-01).
            "image_recertification",
        ):
            if key in previous:
                summary[key] = previous[key]
    unresolved_review = _blocking_ai_review_state(item)
    if unresolved_review is not None:
        summary["blocking"] = True
        review = unresolved_review[1]
        review_messages = [
            str(finding.get("message") or "").strip()
            for finding in (review.get("findings") or [])
            if isinstance(finding, dict) and str(finding.get("message") or "").strip()
        ]
        summary["findings"] = review_messages or [
            "미해결 독립 검수가 있어 자동 재검수가 필요합니다."
        ]
        item.essence_status = ESSENCE_STATUS_NEEDS_REVIEW
    else:
        item.essence_status = screening.status
    item.essence_check_summary = summary
    if hasattr(item, "last_reviewed_philosophy_id"):
        item.last_reviewed_philosophy_id = philosophy.id
    if hasattr(item, "content_revision"):
        item.content_revision = int(getattr(item, "content_revision", 1) or 1) + 1
    return item.essence_status


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
