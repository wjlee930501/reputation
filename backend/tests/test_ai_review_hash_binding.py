"""저장된 독립 검수 PASS는 그 검수가 본 본문에만 유효하다 — 편집된 글은 재검수 전 발행 불가.

PATCH가 본문·제목을 고쳐도 옛 PASS가 그대로 통과시키던 구멍(검수 없이 발행)을 막는다.
공개 중인 글은 같은 이유로 내리지 않는다 — 사후 검수가 이어받는다.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.content import ContentStatus, ContentType
from app.services import content_publication
from app.services.content_ai_review import candidate_review_coverage, candidate_sha256


def _item(**overrides):
    image_hash = "a" * 64
    base = {
        "content_type": ContentType.DISEASE,
        "status": ContentStatus.READY,
        "title": "치질 진료 전 확인할 점",
        "body": "증상과 생활 불편을 확인한 뒤 진료 방향을 설명합니다.",
        "image_url": f"gs://reputation-images/content/{image_hash}-content.png",
        "image_content_hash": image_hash,
        "image_policy_verified_at": datetime.now(timezone.utc),
        "image_policy_version": content_publication.IMAGE_POLICY_VERSION,
        "meta_description": "진료 전 확인할 내용을 정리합니다.",
        "faq_question": None,
        "faq_answer_summary": None,
        "references_list": [{"title": "질병관리청", "url": "https://kdca.go.kr/example"}],
        "content_philosophy_id": None,
        "essence_status": None,
        "essence_check_summary": None,
    }
    base.update(overrides)
    base.setdefault(
        "image_subject_hash",
        content_publication.image_subject_hash(base["content_type"], base["title"]),
    )
    return SimpleNamespace(**base)


def _review(item, **overrides):
    review = {
        "status": "PASS",
        "blocking": False,
        "schema_version": 2,
        "candidate_sha256": candidate_sha256(item),
        "coverage": candidate_review_coverage(item),
        "findings": [],
    }
    review.update(overrides)
    return review


def _aligned(monkeypatch):
    monkeypatch.setattr(
        content_publication,
        "screen_content_against_philosophy",
        lambda *_args: SimpleNamespace(status="ALIGNED", summary={"blocking": False}),
    )


def _assess(item):
    return content_publication.assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))


@pytest.mark.parametrize("review_overrides", [{}, {"status": "REVISE", "blocking": False}])
def test_current_review_still_publishes(monkeypatch, review_overrides):
    _aligned(monkeypatch)
    item = _item()
    item.essence_check_summary = {"ai_review": _review(item, **review_overrides)}

    assert _assess(item).code is None


@pytest.mark.parametrize("review_overrides", [{}, {"status": "REVISE", "blocking": False}])
def test_edited_body_after_pass_is_stale_for_unpublished(monkeypatch, review_overrides):
    _aligned(monkeypatch)
    item = _item()
    item.essence_check_summary = {"ai_review": _review(item, **review_overrides)}
    item.body = "PATCH로 고친 본문입니다. 검수는 이 문장을 본 적이 없습니다."

    assessment = _assess(item)

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_AI_REVIEW_STALE"
    assert content_publication.public_candidate_review_safe(item) is False


def test_edited_title_after_pass_is_stale(monkeypatch):
    _aligned(monkeypatch)
    item = _item()
    item.essence_check_summary = {"ai_review": _review(item)}
    item.title = "고친 제목"

    assert _assess(item).code == "CONTENT_AI_REVIEW_STALE"


def test_legacy_review_without_hash_is_not_stale(monkeypatch):
    """해시 없는 옛 PASS는 그 이유만으로 유료 재검수하지 않는다(CLAUDE.md)."""
    _aligned(monkeypatch)
    item = _item()
    item.essence_check_summary = {"ai_review": {"status": "PASS", "confidence": 0.97}}
    item.body = "고친 본문"

    assert _assess(item).code is None
    assert content_publication.public_candidate_review_safe(item) is True


def test_published_post_edit_is_not_hidden_by_stale_pass():
    """공개 중인 글은 편집 때문에 숨기지 않는다 — 사후 검수가 이어받는다."""
    item = _item(status=ContentStatus.PUBLISHED, published_at=datetime.now(timezone.utc))
    item.essence_check_summary = {"ai_review": _review(item)}
    item.body = "공개 뒤에 고친 본문"

    assert content_publication.public_candidate_review_safe(item) is True


def test_withheld_restore_is_gated_on_current_review(monkeypatch):
    _aligned(monkeypatch)
    item = _item(status=ContentStatus.WITHHELD)
    item.essence_check_summary = {"ai_review": _review(item)}
    item.body = "비공개 중 고친 본문"

    assert _assess(item).code == "CONTENT_AI_REVIEW_STALE"
