"""PATCH가 검수된 발행 전 글을 고치면 야간 재검수 스윕이 집을 표시를 남긴다."""

from app.api.admin.content import _mark_review_stale_after_edit
from app.models.content import ContentStatus
from tests.test_ai_review_hash_binding import _item, _review


def test_patch_marker_lets_nightly_sweep_find_edited_pass():
    item = _item()
    item.essence_check_summary = {"ai_review": _review(item), "keep": 1}
    item.body = "고친 본문"

    _mark_review_stale_after_edit(item)

    assert item.essence_check_summary["ai_review"]["edited_after_review"] is True
    assert item.essence_check_summary["keep"] == 1

    # 원래 본문으로 되돌리면 표시도 걷는다 — 재검수를 사지 않는다.
    item.body = "증상과 생활 불편을 확인한 뒤 진료 방향을 설명합니다."
    _mark_review_stale_after_edit(item)
    assert "edited_after_review" not in item.essence_check_summary["ai_review"]


def test_patch_marker_skips_published_and_legacy():
    published = _item(status=ContentStatus.PUBLISHED)
    published.essence_check_summary = {"ai_review": _review(published)}
    published.body = "공개 뒤 고침"
    _mark_review_stale_after_edit(published)
    assert "edited_after_review" not in published.essence_check_summary["ai_review"]

    legacy = _item()
    legacy.essence_check_summary = {"ai_review": {"status": "PASS"}}
    legacy.body = "고침"
    _mark_review_stale_after_edit(legacy)
    assert legacy.essence_check_summary == {"ai_review": {"status": "PASS"}}
