import uuid
from types import SimpleNamespace

from app.models.essence import PhilosophyStatus
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    ESSENCE_STATUS_MISSING_APPROVED,
    ESSENCE_STATUS_NEEDS_REVIEW,
)
from app.utils.essence_backfill import backfill_hospital_items


def _item(body, **overrides):
    base = dict(
        title=overrides.pop("title", "테스트 콘텐츠"),
        body=body,
        meta_description=overrides.pop("meta_description", None),
        essence_status=None,
        essence_check_summary=None,
        content_philosophy_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _approved_philosophy(avoid_messages=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        version=1,
        status=PhilosophyStatus.APPROVED,
        avoid_messages=avoid_messages or [],
    )


def test_backfill_marks_missing_when_no_approved_philosophy():
    item = _item("환자에게 진료 흐름을 설명합니다.")

    result = backfill_hospital_items([item], approved_philosophy=None)

    assert result.items_total == 1
    assert result.items_updated == 1
    assert result.items_missing_philosophy == 1
    assert item.essence_status == ESSENCE_STATUS_MISSING_APPROVED
    assert item.content_philosophy_id is None
    assert item.essence_check_summary["blocking"] is True


def test_backfill_links_aligned_content_to_approved_philosophy():
    philosophy = _approved_philosophy()
    item = _item("증상을 확인하고 필요한 치료 방향을 함께 결정합니다.")

    result = backfill_hospital_items([item], approved_philosophy=philosophy)

    assert result.items_aligned == 1
    assert item.essence_status == ESSENCE_STATUS_ALIGNED
    assert item.content_philosophy_id == philosophy.id
    assert item.essence_check_summary["blocking"] is False


def test_backfill_flags_legacy_content_with_forbidden_terms_for_review():
    philosophy = _approved_philosophy()
    item = _item("100% 완치를 약속드립니다.")

    result = backfill_hospital_items([item], approved_philosophy=philosophy)

    assert result.items_needs_review == 1
    assert item.essence_status == ESSENCE_STATUS_NEEDS_REVIEW
    assert item.essence_check_summary["blocking"] is True
    # legacy violations must surface so they cannot silently claim alignment
    assert any("의료광고" in finding for finding in item.essence_check_summary["findings"])


def test_backfill_skips_items_without_body():
    philosophy = _approved_philosophy()
    item = _item(None)

    result = backfill_hospital_items([item], approved_philosophy=philosophy)

    assert result.items_skipped_no_body == 1
    assert result.items_updated == 0
    assert item.essence_status is None
    assert item.content_philosophy_id is None
