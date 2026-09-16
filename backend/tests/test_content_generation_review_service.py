"""Provider-injected review sessions preserve paid-call and publication contracts."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models.content import ContentItem, ContentType
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiReview,
    ContentAiReviewStatus,
)
from app.services.content_generation_review import (
    ContentReviewDependencies,
    GenerationReviewLimits,
    generate_reviewed_content,
)
from app.services.cost_guard import CostGuardDecision
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    EssenceScreeningResult,
)

LIMITS = GenerationReviewLimits(3, 2, 1, 1)


def review_result(
    status: ContentAiReviewStatus = ContentAiReviewStatus.PASS,
    findings: tuple[ContentAiFinding, ...] = (),
) -> ContentAiReview:
    return ContentAiReview(status, 0.95, findings, "review", "test-model")


@pytest.fixture
def context() -> dict[str, Any]:
    hospital = Hospital(id=uuid4(), name="격리 검증 의원")
    return {
        "hospital": hospital,
        "item": ContentItem(hospital_id=hospital.id, content_type=ContentType.HEALTH),
        "existing_titles": [],
        "philosophy": HospitalContentPhilosophy(hospital_id=hospital.id),
        "approved_brief": None,
    }


def dependencies(candidate: dict[str, Any] | None = None) -> ContentReviewDependencies:
    data = candidate or {
        "title": "회복 중 생활 안내",
        "body": "## 생활 안내\n생활 습관을 설명합니다.",
    }

    async def generate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return deepcopy(data)

    return ContentReviewDependencies(
        generate=AsyncMock(side_effect=generate),
        review=AsyncMock(return_value=review_result()),
        screen=Mock(return_value=EssenceScreeningResult(ESSENCE_STATUS_ALIGNED, {})),
        check_cost=AsyncMock(return_value=CostGuardDecision(True)),
    )


async def test_first_pass_uses_only_supplied_dependencies(context: dict[str, Any]) -> None:
    effects = dependencies()
    candidate, screening = await generate_reviewed_content(
        **context, dependencies=effects, limits=LIMITS
    )
    assert candidate["title"] == "회복 중 생활 안내"
    assert screening.status == ESSENCE_STATUS_ALIGNED
    effects.generate.assert_awaited_once()
    effects.review.assert_awaited_once()
    effects.screen.assert_called_once()
    effects.check_cost.assert_not_awaited()  # Initial reservation belongs to the worker.
    assert screening.summary["ai_review"]["status"] == "PASS"


async def test_cost_denial_keeps_the_already_reviewed_candidate(context: dict[str, Any]) -> None:
    effects = dependencies(
        {"title": "회복 안내", "body": "내용", "target_alignment_findings": ["다른 각도"]}
    )
    effects.check_cost.return_value = CostGuardDecision(False, "daily limit")
    candidate, screening = await generate_reviewed_content(
        **context, dependencies=effects, limits=LIMITS
    )
    assert candidate["title"] == "회복 안내"
    assert screening.status == ESSENCE_STATUS_ALIGNED
    effects.generate.assert_awaited_once()
    effects.check_cost.assert_awaited_once_with("content")
    assert screening.summary["target_alignment_findings"] == ["다른 각도"]


async def test_paid_soft_rewrite_failure_keeps_the_prior_candidate(context: dict[str, Any]) -> None:
    effects = dependencies()
    first = {"title": "회복 안내", "body": "내용", "target_alignment_findings": ["문체 보완"]}
    effects.generate.side_effect = [first, ValueError("candidate rejected")]
    candidate, screening = await generate_reviewed_content(
        **context, dependencies=effects, limits=LIMITS
    )
    assert candidate is first
    assert screening.status == ESSENCE_STATUS_ALIGNED
    assert effects.generate.await_count == 2
    assert effects.review.await_count == 1
    assert screening.summary["automatic_remediation_attempts"] == 1


async def test_hard_fact_failure_gets_one_removal_and_never_implicit_approval(
    context: dict[str, Any],
) -> None:
    effects = dependencies()
    hard = ContentAiFinding(
        ContentAiFindingSeverity.HARD, ContentAiFindingKind.HOSPITAL_FACT, "근거 없는 경력"
    )
    effects.review.return_value = review_result(ContentAiReviewStatus.REVISE, (hard,))
    _, screening = await generate_reviewed_content(**context, dependencies=effects, limits=LIMITS)
    assert effects.generate.await_count == effects.review.await_count == 2
    assert screening.status == ESSENCE_STATUS_NEEDS_REVIEW
    assert screening.summary["blocking"] is True
    assert screening.summary["hard_removal_rewrites"] == 1
    instructions = effects.generate.await_args_list[1].kwargs["remediation_findings"]
    assert "근거 없는 경력" in instructions


async def test_unavailable_review_is_a_block_not_a_pass(context: dict[str, Any]) -> None:
    effects = dependencies()
    effects.review.return_value = review_result(ContentAiReviewStatus.UNAVAILABLE)
    _, screening = await generate_reviewed_content(**context, dependencies=effects, limits=LIMITS)
    assert screening.status == ESSENCE_STATUS_NEEDS_REVIEW
    assert screening.summary["blocking"] is True
    effects.generate.assert_awaited_once()
    effects.check_cost.assert_not_awaited()


async def test_deterministic_rejection_precedes_paid_independent_review(
    context: dict[str, Any],
) -> None:
    effects = dependencies()
    effects.screen.return_value = EssenceScreeningResult(
        ESSENCE_STATUS_NEEDS_REVIEW, {"findings": ["승인 근거와 불일치"], "blocking": True}
    )
    _, screening = await generate_reviewed_content(**context, dependencies=effects, limits=LIMITS)
    assert screening.status == ESSENCE_STATUS_NEEDS_REVIEW
    assert effects.generate.await_count == 2
    effects.review.assert_not_awaited()


async def test_shared_ceiling_caps_mixed_remediation_modes(context: dict[str, Any]) -> None:
    effects = dependencies(
        {"title": "회복 안내", "body": "내용", "target_alignment_findings": ["문체 보완"]}
    )
    hard = ContentAiFinding(
        ContentAiFindingSeverity.HARD, ContentAiFindingKind.MEDICAL_SAFETY, "근거 없는 효과"
    )
    effects.review.side_effect = [
        review_result(ContentAiReviewStatus.REVISE, (hard,)),
        review_result(),
        review_result(),
    ]
    _, screening = await generate_reviewed_content(**context, dependencies=effects, limits=LIMITS)
    assert effects.generate.await_count == effects.review.await_count == 3
    assert effects.check_cost.await_count == 2
    assert screening.status == ESSENCE_STATUS_ALIGNED
    assert screening.summary["hard_removal_rewrites"] == 1


async def test_reference_advice_removes_only_named_reference_without_rewriting(
    context: dict[str, Any],
) -> None:
    effects = dependencies(
        {
            "title": "회복 안내",
            "body": "내용",
            "references": [{"title": "무관한 참고문헌"}, {"title": "관련 있는 참고문헌"}],
        }
    )
    finding = ContentAiFinding(
        ContentAiFindingSeverity.SOFT, ContentAiFindingKind.REFERENCE, "무관한 참고문헌 삭제 권고"
    )
    effects.review.return_value = review_result(findings=(finding,))
    candidate, screening = await generate_reviewed_content(
        **context, dependencies=effects, limits=LIMITS
    )
    assert candidate["references"] == [{"title": "관련 있는 참고문헌"}]
    assert screening.status == ESSENCE_STATUS_ALIGNED
    effects.generate.assert_awaited_once()
    effects.check_cost.assert_not_awaited()


async def test_last_reference_is_not_removed_by_nonblocking_advice(context: dict[str, Any]) -> None:
    effects = dependencies(
        {"title": "회복 안내", "body": "내용", "references": [{"title": "유일한 참고문헌"}]}
    )
    finding = ContentAiFinding(
        ContentAiFindingSeverity.SOFT, ContentAiFindingKind.REFERENCE, "유일한 참고문헌 확인 권고"
    )
    effects.review.return_value = review_result(findings=(finding,))
    candidate, screening = await generate_reviewed_content(
        **context, dependencies=effects, limits=LIMITS
    )
    assert candidate["references"] == [{"title": "유일한 참고문헌"}]
    assert screening.status == ESSENCE_STATUS_ALIGNED
    effects.generate.assert_awaited_once()


async def test_generation_errors_without_a_candidate_still_propagate(
    context: dict[str, Any],
) -> None:
    effects = dependencies()
    effects.generate.side_effect = ValueError("deterministic rejection")
    with pytest.raises(ValueError, match="deterministic rejection"):
        await generate_reviewed_content(**context, dependencies=effects, limits=LIMITS)
    assert effects.generate.await_count == 2
    effects.review.assert_not_awaited()


async def test_provider_runtime_error_is_not_swallowed(context: dict[str, Any]) -> None:
    effects = dependencies()
    effects.generate.side_effect = RuntimeError("provider failed")
    with pytest.raises(RuntimeError, match="provider failed"):
        await generate_reviewed_content(**context, dependencies=effects, limits=LIMITS)
    effects.generate.assert_awaited_once()


async def test_caller_supplied_limit_is_not_replaced_by_hidden_defaults(
    context: dict[str, Any],
) -> None:
    effects = dependencies(
        {"title": "회복 안내", "body": "내용", "target_alignment_findings": ["문체 보완"]}
    )
    await generate_reviewed_content(
        **context, dependencies=effects, limits=replace(LIMITS, max_generations=1)
    )
    effects.generate.assert_awaited_once()
    effects.check_cost.assert_not_awaited()


def test_session_dependencies_and_budgets_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        LIMITS.max_generations = 99
    effects = dependencies()
    with pytest.raises(FrozenInstanceError):
        effects.generate = AsyncMock()
