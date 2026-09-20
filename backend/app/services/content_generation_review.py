"""Bounded generation/review workflow with explicit provider and budget boundaries.

This service never claims work, commits a session, publishes content, or sends
notifications. The Celery adapter owns those effects and supplies dependencies.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.models.content import ContentItem
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.services.content_ai_review import ContentAiReview, ContentAiReviewStatus
from app.services.content_engine import SEASON_MISMATCH_FINDING_PREFIX
from app.services.content_review_feedback import (
    apply_reference_review_findings,
    duplicate_topic_matches,
    duplicate_topic_remediation,
    hard_removal_findings,
    non_reference_remediation_messages,
    review_findings,
    screening_probe,
    writer_remediation_findings,
)
from app.services.cost_guard import CostGuardDecision
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    EssenceScreeningResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContentReviewDependencies:
    """Effects supplied by the caller, resolved once for each generation session."""

    generate: Callable[..., Awaitable[dict[str, Any]]]
    review: Callable[..., Awaitable[ContentAiReview]]
    screen: Callable[[ContentItem, HospitalContentPhilosophy], EssenceScreeningResult]
    check_cost: Callable[[str], Awaitable[CostGuardDecision]]


@dataclass(frozen=True, slots=True)
class GenerationReviewLimits:
    """Shared paid-generation ceiling and existing remediation sub-budgets."""

    max_generations: int
    remediation_generations: int
    soft_rewrites: int
    hard_removals: int


async def generate_reviewed_content(
    *,
    hospital: Hospital,
    item: ContentItem,
    existing_titles: list[str],
    philosophy: HospitalContentPhilosophy,
    approved_brief: dict | None,
    dependencies: ContentReviewDependencies,
    limits: GenerationReviewLimits,
) -> tuple[dict[str, Any], EssenceScreeningResult]:
    """Generate, independently review, and rewrite without bypassing hard gates."""

    # 저장된 차단을 작가가 할 수 있는 일로 옮겨 넘긴다. 게이트의 운영자 문장을 그대로
    # 주면 작가가 승인 자료에 없는 사실을 지어내 검증기에 걸린다.
    findings = writer_remediation_findings(getattr(item, "essence_check_summary", None))
    automatic_rewrites = int(bool(findings))
    reviewer_driven_rewrites = 0
    removal_rewrites = 0
    # 하드 게이트(Essence 스크린)와 독립 검수를 **먼저** 통과시킨 뒤에야 문체성
    # 키워드·계절 보완에 재작성을 쓴다. 반대 순서로 쓰면 보완 예산을 문체 지적이
    # 먼저 소모해 정작 발행을 막는 지적은 한 번도 고치지 못한 채 글이 버려진다.
    remediation_rewrites = 0
    soft_rewrites = 0
    generations = 0
    accepted_content: dict | None = None
    accepted_screening = None
    accepted_reference_findings: list[str] = []
    last_ai_review = None
    last_generation_error: Exception | None = None

    def _generation_budget_left() -> bool:
        return generations < limits.max_generations

    def _remediation_budget_left() -> bool:
        return (
            remediation_rewrites < limits.remediation_generations - 1 and _generation_budget_left()
        )

    while _generation_budget_left():
        if generations > 0:
            decision = await dependencies.check_cost("content")
            if not decision.allowed:
                logger.info(
                    "Automatic content remediation stopped by cost guard: hospital=%s",
                    hospital.id,
                )
                break
            automatic_rewrites += 1

        try:
            candidate = await dependencies.generate(
                hospital,
                item.content_type,
                existing_titles,
                philosophy,
                approved_brief,
                remediation_findings=findings,
            )
        except ValueError as exc:
            generations += 1
            last_generation_error = exc
            findings = [f"생성 안전검사 실패: {' '.join(str(exc).split())[:300]}"]
            if accepted_content is not None:
                # 앞선 회차가 만들어 둔(이미 결제된) 후보가 있다. 보완 재작성이 실패했다고
                # 그 후보까지 버리면 정상 글 한 편을 돈만 쓰고 폐기하는 셈이다.
                last_generation_error = None
                break
            if _remediation_budget_left():
                remediation_rewrites += 1
                continue
            raise
        generations += 1
        last_generation_error = None

        # 이 글이 원래 답하기로 한 측정 질문을 실제로 다뤘는가.
        # (content_engine._validate_target_alignment가 채운다)
        bounded_soft_findings = list(candidate.get("target_alignment_findings") or []) + [
            finding
            for finding in (candidate.get("seo_geo_findings") or [])
            if str(finding).startswith(SEASON_MISMATCH_FINDING_PREFIX)
        ]

        screening = dependencies.screen(screening_probe(candidate), philosophy)
        accepted_content, accepted_screening, last_ai_review = candidate, screening, None
        accepted_reference_findings = []
        if screening.status != ESSENCE_STATUS_ALIGNED:
            screen_findings = review_findings(screening.summary)
            if screen_findings and _remediation_budget_left():
                remediation_rewrites += 1
                findings = screen_findings
                continue
            break

        last_ai_review = await dependencies.review(
            hospital=hospital,
            philosophy=philosophy,
            content=candidate,
            content_brief=approved_brief,
        )
        if last_ai_review.status == ContentAiReviewStatus.UNAVAILABLE:
            # UNAVAILABLE never grants approval: it merely leaves the candidate to
            # the deterministic generation and publication gates below.
            break
        # 참고자료 주제 불일치(SOFT+REFERENCE)는 유료 재작성이 아니라 결정적 제거로 푼다.
        accepted_reference_findings = apply_reference_review_findings(candidate, last_ai_review)
        if last_ai_review.status == ContentAiReviewStatus.PASS:
            # 중복 주제도 문체성 지적과 같은 한 번의 보완 예산을 나눠 쓴다. 새 예산을
            # 만들지 않으며, 고치지 못해도 글은 통과시키고 기록만 남긴다.
            duplicate_remediation = (
                duplicate_topic_remediation(
                    duplicate_topic_matches(candidate, existing_titles, approved_brief)
                )
                if soft_rewrites < limits.soft_rewrites
                else []
            )
            soft_findings = bounded_soft_findings + duplicate_remediation
            if soft_findings and soft_rewrites < limits.soft_rewrites and _generation_budget_left():
                soft_rewrites += 1
                findings = soft_findings
                continue
            break
        # Only stylistic/soft feedback may spend the shared remediation rewrite.
        if last_ai_review.rewrite_is_safe:
            rewrite_messages = non_reference_remediation_messages(last_ai_review)
            if rewrite_messages and _remediation_budget_left():
                remediation_rewrites += 1
                reviewer_driven_rewrites += 1
                findings = rewrite_messages
                continue
            break
        # 사실·의료 안전 HARD 지적은 새 근거 없이 "다시 써 봐"로 풀 수 없다. 대신
        # 지적된 주장을 삭제·hedge하는 재작성을 **정확히 한 번** 허용하고 그 결과는
        # 반드시 독립 검수를 다시 받는다(루프 다음 회차).
        removal_findings = hard_removal_findings(last_ai_review)
        if (
            removal_findings
            and removal_rewrites < limits.hard_removals
            and _generation_budget_left()
        ):
            removal_rewrites += 1
            findings = removal_findings
            continue
        break

    last_content = accepted_content
    last_screening = accepted_screening

    if last_content is None or last_screening is None:
        if last_generation_error is not None:
            raise last_generation_error
        raise RuntimeError("automatic content review produced no candidate")

    if last_ai_review and (
        last_ai_review.blocking_findings
        or last_ai_review.status == ContentAiReviewStatus.UNAVAILABLE
    ):
        summary = dict(last_screening.summary or {})
        summary.update(
            {
                "blocking": True,
                "findings": (
                    list(last_ai_review.remediation_messages)
                    if last_ai_review.blocking_findings
                    else ["독립 AI 검수를 완료하지 못해 자동 재검수가 필요합니다."]
                ),
            }
        )
        last_screening = type(last_screening)(
            status=ESSENCE_STATUS_NEEDS_REVIEW,
            summary=summary,
        )

    summary = dict(last_screening.summary or {})
    # 보완 재작성 후에도 키워드가 주제 위치에 없으면 글은 살리고 기록만 남긴다.
    # Admin의 콘텐츠 상세가 essence_check_summary를 그대로 보여주므로 AE가 확인할 수 있다.
    residual_alignment = list(last_content.get("target_alignment_findings") or [])
    if residual_alignment:
        summary["target_alignment_findings"] = residual_alignment
    residual_season = [
        finding
        for finding in (last_content.get("seo_geo_findings") or [])
        if str(finding).startswith(SEASON_MISMATCH_FINDING_PREFIX)
    ]
    if residual_season:
        summary["season_title_findings"] = residual_season
    # 중복 주제는 발행을 막지 않는다 — 재작성 뒤에도 남으면 글은 통과시키고 AE가 볼 수
    # 있게 기록만 남긴다(거절 코드·인시던트·Slack 없음).
    residual_duplicates = duplicate_topic_matches(last_content, existing_titles, approved_brief)
    if residual_duplicates:
        summary["duplicate_topic_findings"] = [
            {"title": title, "score": score} for title, score in residual_duplicates
        ]
    if accepted_reference_findings:
        summary["reference_findings"] = accepted_reference_findings[:5]
    if automatic_rewrites > 0:
        summary["automatic_remediation_attempts"] = automatic_rewrites
    if reviewer_driven_rewrites > 0:
        summary["reviewer_driven_rewrites"] = reviewer_driven_rewrites
    if removal_rewrites > 0:
        summary["hard_removal_rewrites"] = removal_rewrites
    if last_ai_review is not None:
        summary["ai_review"] = last_ai_review.payload()
    reviewed_screening = type(last_screening)(
        status=last_screening.status,
        summary=summary,
    )
    return last_content, reviewed_screening
