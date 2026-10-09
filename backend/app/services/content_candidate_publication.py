"""Typed boundary between mutable candidates and immutable approved editions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Mapping

from pydantic import ValidationError
from sqlalchemy import text, update

from app.models.content import ContentItem, ContentStatus
from app.schemas.content import PendingCandidateReview, PendingContentCandidate
from app.services.content_ai_review import (
    ContentAiReview,
    ContentAiReviewStatus,
    candidate_sha256,
)
from app.services.content_publication import (
    apply_publication_assessment,
    assess_content_publication,
)
from app.services.reference_publication import bind_reference_checks_to_revision


@dataclass(frozen=True, slots=True)
class CandidateStageConflict:
    reason: str


@dataclass(frozen=True, slots=True)
class ActivePublicText:
    title: str
    body: str
    meta_description: str | None
    faq_question: str | None
    faq_answer_summary: str | None
    references_list: list[dict[str, Any]]
    reference_checks: list[dict[str, Any]]
    approval_hash: str


@dataclass(frozen=True, slots=True)
class CandidateApprovalApplied:
    previous_revision_id: uuid.UUID
    candidate_sha256: str
    content_revision: int


@dataclass(frozen=True, slots=True)
class CandidateApprovalRejected:
    reason: str


def active_public_text(item: Any) -> ActivePublicText | None:
    """Return only approved text; legacy objects without pointer support retain compatibility."""

    if hasattr(item, "active_revision_id"):
        revision = getattr(item, "active_revision", None)
        if revision is None or getattr(item, "active_revision_id", None) is None:
            return None
        source = revision
        approval_hash = str(revision.approval_hash)
    else:
        source = item
        approval_hash = candidate_sha256(item)
    title = str(getattr(source, "title", "") or "")
    body = str(getattr(source, "body", "") or "")
    if not title.strip() or not body.strip():
        return None
    return ActivePublicText(
        title=title,
        body=body,
        meta_description=getattr(source, "meta_description", None),
        faq_question=getattr(source, "faq_question", None),
        faq_answer_summary=getattr(source, "faq_answer_summary", None),
        references_list=list(getattr(source, "references_list", None) or []),
        reference_checks=list(getattr(source, "reference_checks", None) or []),
        approval_hash=approval_hash,
    )


def parse_pending_candidate(item: Any) -> PendingContentCandidate | None:
    raw = getattr(item, "pending_revision", None)
    if raw is None:
        return None
    try:
        return PendingContentCandidate.model_validate(raw)
    except ValidationError:
        return None


def pending_candidate_content(candidate: PendingContentCandidate) -> dict[str, Any]:
    """Return the exact machine-reviewed fields in their JSON representation."""

    payload = candidate.model_dump(mode="json")
    return {
        "title": payload["title"],
        "body": payload["body"],
        "meta_description": payload["meta_description"],
        "faq_question": payload["faq_question"],
        "faq_answer_summary": payload["faq_answer_summary"],
        "references_list": payload["references_list"],
    }


def stage_pending_candidate(
    item: Any,
    *,
    expected_active_revision_id: uuid.UUID,
    expected_content_revision: int,
    title: str,
    body: str,
    meta_description: str | None,
    faq_question: str | None,
    faq_answer_summary: str | None,
    references_list: list[dict[str, Any]],
    reference_checks: list[dict[str, Any]],
    created_by: str,
    created_at: datetime,
) -> PendingContentCandidate | CandidateStageConflict:
    """CAS-stage a candidate without changing the active pointer or compatibility mirror."""

    if (
        getattr(item, "active_revision_id", None) != expected_active_revision_id
        or int(getattr(item, "content_revision", 0) or 0) != expected_content_revision
    ):
        return CandidateStageConflict(reason="stale_active_revision")
    candidate_values = {
        "title": title,
        "body": body,
        "meta_description": meta_description,
        "faq_question": faq_question,
        "faq_answer_summary": faq_answer_summary,
        "references_list": references_list,
    }
    pending = PendingContentCandidate(
        base_active_revision_id=expected_active_revision_id,
        base_content_revision=expected_content_revision,
        candidate_sha256=candidate_sha256(SimpleNamespace(**candidate_values)),
        title=title,
        body=body,
        meta_description=meta_description,
        faq_question=faq_question,
        faq_answer_summary=faq_answer_summary,
        references_list=references_list,
        reference_checks=reference_checks,
        created_by=created_by,
        created_at=created_at,
    )
    item.pending_revision = pending.model_dump(mode="json")
    return pending


async def stage_pending_candidate_cas(
    db: Any,
    item: ContentItem,
    *,
    expected_active_revision_id: uuid.UUID,
    expected_content_revision: int,
    title: str,
    body: str,
    meta_description: str | None,
    faq_question: str | None,
    faq_answer_summary: str | None,
    references_list: list[dict[str, Any]],
    reference_checks: list[dict[str, Any]],
    created_by: str,
    created_at: datetime,
) -> PendingContentCandidate | CandidateStageConflict:
    """Persist one candidate only if the caller's approved pointer snapshot is current."""

    if (
        item.active_revision_id != expected_active_revision_id
        or int(item.content_revision or 0) != expected_content_revision
    ):
        return CandidateStageConflict(reason="stale_active_revision")
    candidate_values = {
        "title": title,
        "body": body,
        "meta_description": meta_description,
        "faq_question": faq_question,
        "faq_answer_summary": faq_answer_summary,
        "references_list": references_list,
    }
    staged = PendingContentCandidate(
        base_active_revision_id=expected_active_revision_id,
        base_content_revision=expected_content_revision,
        candidate_sha256=candidate_sha256(SimpleNamespace(**candidate_values)),
        title=title,
        body=body,
        meta_description=meta_description,
        faq_question=faq_question,
        faq_answer_summary=faq_answer_summary,
        references_list=references_list,
        reference_checks=reference_checks,
        created_by=created_by,
        created_at=created_at,
    )
    payload = staged.model_dump(mode="json")
    result = await db.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item.id,
            ContentItem.active_revision_id == staged.base_active_revision_id,
            ContentItem.content_revision == staged.base_content_revision,
        )
        .values(pending_revision=payload)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await db.refresh(item, attribute_names=["pending_revision"])
        return CandidateStageConflict(reason="stale_active_revision")
    await db.refresh(item, attribute_names=["pending_revision"])
    return staged


async def cancel_pending_candidate_cas(
    db: Any,
    item: ContentItem,
    *,
    expected_candidate_sha256: str,
) -> bool:
    """Remove exactly the candidate the operator saw without changing the active edition."""

    pending = parse_pending_candidate(item)
    if pending is None or pending.candidate_sha256 != expected_candidate_sha256:
        return False
    payload = pending.model_dump(mode="json")
    result = await db.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item.id,
            ContentItem.active_revision_id == pending.base_active_revision_id,
            ContentItem.content_revision == pending.base_content_revision,
            ContentItem.pending_revision == payload,
        )
        .values(pending_revision=None)
        .execution_options(synchronize_session=False)
    )
    await db.refresh(item, attribute_names=["pending_revision"])
    return result.rowcount == 1


def reject_pending_candidate(
    item: Any,
    *,
    expected_candidate_sha256: str,
    review_payload: Mapping[str, Any],
    reviewed_at: datetime,
) -> bool:
    """Record a bound rejection on the candidate while leaving approved text untouched."""

    pending = parse_pending_candidate(item)
    if pending is None or pending.candidate_sha256 != expected_candidate_sha256:
        return False
    review = PendingCandidateReview.model_validate(
        {**review_payload, "reviewed_at": reviewed_at}
    )
    item.pending_revision = pending.model_copy(update={"review": review}).model_dump(mode="json")
    return True


def apply_approved_candidate_fields(
    item: Any,
    review: ContentAiReview,
    *,
    philosophy: Any,
    approved_by: str,
    approved_at: datetime,
) -> CandidateApprovalApplied | CandidateApprovalRejected | CandidateStageConflict:
    """CAS-apply a PASS candidate to mirrors; caller must append its immutable edition."""

    pending = parse_pending_candidate(item)
    if pending is None:
        return CandidateApprovalRejected(reason="missing_or_malformed_candidate")
    if pending.base_active_revision_id != getattr(
        item, "active_revision_id", None
    ) or pending.base_content_revision != int(getattr(item, "content_revision", 0) or 0):
        return CandidateStageConflict(reason="stale_active_revision")
    content = pending_candidate_content(pending)
    if (
        review.status != ContentAiReviewStatus.PASS
        or review.blocking_findings
        or review.candidate_sha256 != pending.candidate_sha256
        or candidate_sha256(content) != pending.candidate_sha256
    ):
        return CandidateApprovalRejected(reason="review_not_bound_pass")

    candidate_view = SimpleNamespace(**vars(item))
    for field, value in content.items():
        setattr(candidate_view, field, value)
    candidate_view.reference_checks = pending.model_dump(mode="json")["reference_checks"]
    candidate_view.active_revision_id = None
    if bind_reference_checks_to_revision(candidate_view) is None:
        return CandidateApprovalRejected(reason="reference_evidence_not_bound")
    summary = (
        dict(getattr(item, "essence_check_summary", None))
        if isinstance(getattr(item, "essence_check_summary", None), dict)
        else {}
    )
    summary["ai_review"] = review.payload()
    summary.pop("post_publish_ai_review", None)
    candidate_view.essence_check_summary = summary
    assessment = assess_content_publication(candidate_view, philosophy)
    if not assessment.publishable:
        return CandidateApprovalRejected(reason=f"not_publishable:{assessment.code}")

    previous_revision_id = pending.base_active_revision_id
    for field, value in content.items():
        setattr(item, field, value)
    item.reference_checks = pending.model_dump(mode="json")["reference_checks"]
    item.essence_check_summary = summary
    apply_publication_assessment(item, assessment)
    item.content_revision = pending.base_content_revision + 1
    item.body_updated_at = approved_at
    item.post_publish_reviewed_at = approved_at
    item.post_publish_reviewed_by = approved_by
    item.pending_revision = None
    return CandidateApprovalApplied(
        previous_revision_id=previous_revision_id,
        candidate_sha256=pending.candidate_sha256,
        content_revision=item.content_revision,
    )


def publish_pending_candidate_sync(
    db: Any,
    item: Any,
    review: ContentAiReview,
    *,
    philosophy: Any,
    approved_by: str,
    approved_at: datetime,
) -> CandidateApprovalApplied | CandidateApprovalRejected | CandidateStageConflict:
    """Apply mirrors and atomically append/swap the immutable revision in one transaction."""

    if getattr(item, "status", None) != ContentStatus.PUBLISHED:
        return CandidateApprovalRejected(reason="content_not_published")
    outcome = apply_approved_candidate_fields(
        item,
        review,
        philosophy=philosophy,
        approved_by=approved_by,
        approved_at=approved_at,
    )
    if not isinstance(outcome, CandidateApprovalApplied):
        return outcome
    item.active_revision_id = None
    db.flush()
    reconciled = db.execute(
        text("SELECT * FROM reconcile_content_revisions(:content_item_id)"),
        {"content_item_id": item.id},
    ).one()
    if reconciled.created_count != 1:
        return CandidateApprovalRejected(reason="approved_revision_not_created")
    db.refresh(item, attribute_names=["active_revision_id"])
    if item.active_revision_id == outcome.previous_revision_id:
        return CandidateApprovalRejected(reason="active_revision_not_swapped")
    db.expire(item, ["active_revision"])
    return outcome


async def publish_pending_candidate(
    db: Any,
    item: Any,
    review: ContentAiReview,
    *,
    philosophy: Any,
    approved_by: str,
    approved_at: datetime,
) -> CandidateApprovalApplied | CandidateApprovalRejected | CandidateStageConflict:
    """Async counterpart used by the Admin/API transaction boundary."""

    if getattr(item, "status", None) != ContentStatus.PUBLISHED:
        return CandidateApprovalRejected(reason="content_not_published")
    outcome = apply_approved_candidate_fields(
        item,
        review,
        philosophy=philosophy,
        approved_by=approved_by,
        approved_at=approved_at,
    )
    if not isinstance(outcome, CandidateApprovalApplied):
        return outcome
    item.active_revision_id = None
    await db.flush()
    reconciled = (
        await db.execute(
            text("SELECT * FROM reconcile_content_revisions(:content_item_id)"),
            {"content_item_id": item.id},
        )
    ).one()
    if reconciled.created_count != 1:
        return CandidateApprovalRejected(reason="approved_revision_not_created")
    await db.refresh(item, attribute_names=["active_revision_id"])
    if item.active_revision_id == outcome.previous_revision_id:
        return CandidateApprovalRejected(reason="active_revision_not_swapped")
    db.expire(item, ["active_revision"])
    return outcome
