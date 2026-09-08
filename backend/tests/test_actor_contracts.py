import pytest
from pydantic import ValidationError

from app.api.admin.content import PublishBody
from app.schemas.essence import PhilosophyApprove


def test_philosophy_approval_actor_and_evidence_confirmation_are_required():
    with pytest.raises(ValidationError) as missing_actor:
        PhilosophyApprove(
            reviewed_by="",
            approval_note="검토자 없이 승인 시도",
            confirm_evidence_reviewed=True,
        )
    assert "reviewed_by" in str(missing_actor.value)

    with pytest.raises(ValidationError) as missing_evidence_confirmation:
        PhilosophyApprove(
            reviewed_by="Ops Lead",
            approval_note="근거 확인 없이 승인 시도",
            confirm_evidence_reviewed=False,
        )
    assert "근거 검토 확인" in str(missing_evidence_confirmation.value)


def test_publish_body_cannot_claim_a_publisher():
    """발행자는 요청 본문이 아니라 확인된 요청 actor로 기록한다 (H-09)."""
    assert not hasattr(PublishBody(), "published_by")
    assert not hasattr(PublishBody(published_by="김민지 AE"), "published_by")
