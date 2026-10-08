"""Candidate revisions keep the last approved edition public until one atomic PASS."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.content import ContentStatus
from app.services import content_publication
from app.services.content_ai_review import (
    ContentAiReview,
    ContentAiReviewStatus,
    candidate_review_coverage,
    candidate_sha256,
)
from app.services.content_candidate_publication import (
    CandidateApprovalApplied,
    CandidateApprovalRejected,
    CandidateStageConflict,
    PendingContentCandidate,
    active_public_text,
    apply_approved_candidate_fields,
    pending_candidate_content,
    reject_pending_candidate,
    stage_pending_candidate,
)
from app.services.reference_verification import item_topic_fingerprint, reference_check_record

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _item() -> SimpleNamespace:
    active_id = uuid.uuid4()
    active = SimpleNamespace(
        id=active_id,
        title="승인 제목",
        body="승인 본문",
        meta_description="승인 설명",
        faq_question=None,
        faq_answer_summary=None,
        references_list=[{"title": "질병관리청", "url": "https://kdca.go.kr/a"}],
        reference_checks=[{"url": "https://kdca.go.kr/a", "verdict": "PASS"}],
        approval_hash="a" * 64,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=ContentStatus.PUBLISHED,
        active_revision_id=active_id,
        active_revision=active,
        content_revision=7,
        pending_revision=None,
        title=active.title,
        body=active.body,
        meta_description=active.meta_description,
        faq_question=active.faq_question,
        faq_answer_summary=active.faq_answer_summary,
        references_list=active.references_list,
        reference_checks=active.reference_checks,
        content_type="DISEASE",
        content_philosophy_id=uuid.uuid4(),
        last_reviewed_philosophy_id=None,
        essence_status="ALIGNED",
        essence_check_summary={"generation_provenance": {"evidence_note_ids": ["note-1"]}},
        image_url=None,
        published_at=NOW,
        post_publish_reviewed_at=None,
        post_publish_reviewed_by=None,
    )


def _pass(candidate: PendingContentCandidate) -> ContentAiReview:
    content = pending_candidate_content(candidate)
    return ContentAiReview(
        status=ContentAiReviewStatus.PASS,
        confidence=0.95,
        findings=(),
        summary="통과",
        model="test",
        candidate_sha256=candidate_sha256(content),
        coverage=candidate_review_coverage(content),
    )


def test_stage_pending_candidate_preserves_active_public_text() -> None:
    item = _item()

    outcome = stage_pending_candidate(
        item,
        expected_active_revision_id=item.active_revision_id,
        expected_content_revision=7,
        title="후보 제목",
        body="후보 본문",
        meta_description="후보 설명",
        faq_question=None,
        faq_answer_summary=None,
        references_list=item.references_list,
        reference_checks=item.reference_checks,
        created_by="ae@example.com",
        created_at=NOW,
    )

    assert isinstance(outcome, PendingContentCandidate)
    assert active_public_text(item).body == "승인 본문"
    assert item.body == "승인 본문"
    assert item.active_revision_id == item.active_revision.id
    assert item.pending_revision["body"] == "후보 본문"
    assert item.pending_revision["candidate_sha256"] == candidate_sha256(
        pending_candidate_content(outcome)
    )


def test_stage_pending_candidate_rejects_a_stale_editor_without_mutation() -> None:
    item = _item()
    before = vars(item).copy()

    outcome = stage_pending_candidate(
        item,
        expected_active_revision_id=uuid.uuid4(),
        expected_content_revision=6,
        title="늦게 온 후보",
        body="늦게 온 본문",
        meta_description=None,
        faq_question=None,
        faq_answer_summary=None,
        references_list=item.references_list,
        reference_checks=item.reference_checks,
        created_by="ae@example.com",
        created_at=NOW,
    )

    assert isinstance(outcome, CandidateStageConflict)
    assert vars(item) == before


def test_rejected_candidate_keeps_active_pointer_and_public_hash() -> None:
    item = _item()
    candidate = stage_pending_candidate(
        item,
        expected_active_revision_id=item.active_revision_id,
        expected_content_revision=7,
        title="검수 실패 후보",
        body="근거 없는 후보 본문",
        meta_description=None,
        faq_question=None,
        faq_answer_summary=None,
        references_list=item.references_list,
        reference_checks=item.reference_checks,
        created_by="ae@example.com",
        created_at=NOW,
    )
    active_hash = active_public_text(item).approval_hash

    reject_pending_candidate(
        item,
        expected_candidate_sha256=candidate.candidate_sha256,
        review_payload={"status": "REVISE", "candidate_sha256": candidate.candidate_sha256},
        reviewed_at=NOW,
    )

    assert active_public_text(item).approval_hash == active_hash
    assert item.body == "승인 본문"
    assert item.pending_revision["review"]["status"] == "REVISE"


def test_bound_pass_updates_mirror_only_after_candidate_review(monkeypatch) -> None:
    item = _item()
    checked_at = datetime.now(UTC)
    candidate_view = SimpleNamespace(
        **{
            **vars(item),
            "title": "승인될 후보",
            "body": "검수를 통과한 후보 본문",
            "faq_question": None,
        }
    )
    candidate_checks = [
        reference_check_record(
            item.references_list[0]["url"],
            verdict="pass",
            reason="page_verified",
            checked_at=checked_at,
            curated=False,
            status=200,
            final_url=item.references_list[0]["url"],
            page_title="질병관리청 진료 안내",
            text_len=500,
            verified_at=checked_at,
            topic_fingerprint=item_topic_fingerprint(candidate_view),
        )
    ]
    candidate = stage_pending_candidate(
        item,
        expected_active_revision_id=item.active_revision_id,
        expected_content_revision=7,
        title="승인될 후보",
        body="검수를 통과한 후보 본문",
        meta_description="후보 설명",
        faq_question=None,
        faq_answer_summary=None,
        references_list=item.references_list,
        reference_checks=candidate_checks,
        created_by="ae@example.com",
        created_at=NOW,
    )
    monkeypatch.setattr(
        content_publication,
        "screen_content_against_philosophy",
        lambda *_args: SimpleNamespace(status="ALIGNED", summary={"blocking": False}),
    )
    philosophy = SimpleNamespace(id=item.content_philosophy_id)

    outcome = apply_approved_candidate_fields(
        item,
        _pass(candidate),
        philosophy=philosophy,
        approved_by="system:ai-review",
        approved_at=NOW,
    )

    assert isinstance(outcome, CandidateApprovalApplied)
    assert item.body == "검수를 통과한 후보 본문"
    assert item.content_revision == 8
    assert item.pending_revision is None
    assert item.active_revision_id == outcome.previous_revision_id


def test_changed_claim_without_fresh_reference_evidence_is_rejected(monkeypatch) -> None:
    item = _item()
    candidate = stage_pending_candidate(
        item,
        expected_active_revision_id=item.active_revision_id,
        expected_content_revision=7,
        title="근거가 바뀐 후보",
        body="새 의료 주장을 담은 후보 본문",
        meta_description="새 설명",
        faq_question=None,
        faq_answer_summary=None,
        references_list=item.references_list,
        reference_checks=item.reference_checks,
        created_by="ae@example.com",
        created_at=NOW,
    )
    monkeypatch.setattr(
        content_publication,
        "screen_content_against_philosophy",
        lambda *_args: SimpleNamespace(status="ALIGNED", summary={"blocking": False}),
    )

    outcome = apply_approved_candidate_fields(
        item,
        _pass(candidate),
        philosophy=SimpleNamespace(id=item.content_philosophy_id),
        approved_by="system:ai-review",
        approved_at=NOW,
    )

    assert outcome == CandidateApprovalRejected(reason="reference_evidence_not_bound")
    assert item.active_revision_id == item.active_revision.id
    assert item.title == "승인 제목"


def test_pass_for_another_hash_never_changes_the_mirror(monkeypatch) -> None:
    item = _item()
    candidate = stage_pending_candidate(
        item,
        expected_active_revision_id=item.active_revision_id,
        expected_content_revision=7,
        title="후보",
        body="후보 본문",
        meta_description=None,
        faq_question=None,
        faq_answer_summary=None,
        references_list=item.references_list,
        reference_checks=item.reference_checks,
        created_by="ae@example.com",
        created_at=NOW,
    )
    review = _pass(candidate)
    review = ContentAiReview(
        status=review.status,
        confidence=review.confidence,
        findings=review.findings,
        summary=review.summary,
        model=review.model,
        candidate_sha256="f" * 64,
        coverage=review.coverage,
    )
    before = vars(item).copy()

    outcome = apply_approved_candidate_fields(
        item,
        review,
        philosophy=SimpleNamespace(id=item.content_philosophy_id),
        approved_by="system:ai-review",
        approved_at=NOW,
    )

    assert isinstance(outcome, CandidateApprovalRejected)
    assert vars(item) == before
