from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.models.content import ContentStatus
from app.services.monthly_content_operations import (
    build_monthly_content_operations_snapshot,
)


def _item(
    *,
    status=ContentStatus.PUBLISHED,
    sequence_no=1,
    reviewed_at=None,
    body_updated_at=None,
):
    published_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        status=status,
        scheduled_date=date(2026, 7, sequence_no),
        sequence_no=sequence_no,
        published_at=published_at if status == ContentStatus.PUBLISHED else None,
        post_publish_reviewed_at=reviewed_at,
        post_publish_reviewed_by="ae@example.com" if reviewed_at else None,
        body_updated_at=body_updated_at,
    )


def test_monthly_content_operations_warns_on_pending_required_review_sample_without_blocking():
    cutoff = datetime(2026, 8, 1, 0, 15, tzinfo=timezone.utc)
    reviewed = _item(reviewed_at=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc))
    # Sequence 2 no longer belongs to the sample on its own — a rewrite that the automatic
    # gate remediated is evidence the gate worked, not a reason to sample it. A body edit
    # after publication is still an AE-observable event worth sampling.
    pending = _item(
        sequence_no=2,
        body_updated_at=datetime(2026, 7, 10, 2, 0, tzinfo=timezone.utc),
    )
    draft = _item(status=ContentStatus.DRAFT, sequence_no=3)

    snapshot = build_monthly_content_operations_snapshot(
        plan="PLAN_12",
        scheduled_items=[reviewed, pending, draft],
        published_items=[reviewed, pending],
        cutoff_at=cutoff,
    )

    assert snapshot.payload["plan_quota"] == 12
    assert snapshot.payload["published_count"] == 2
    assert snapshot.payload["shortfall_count"] == 10
    assert snapshot.payload["scheduled_slot_state_counts"] == {"DRAFT": 1, "PUBLISHED": 2}
    review = snapshot.payload["post_publish_review"]
    assert review["required_sample_count"] == 2
    assert review["reviewed_count"] == 1
    assert review["pending_count"] == 1
    assert review["overdue_count"] == 1
    # A pending post-publish sample is observability sampling, not a second approval queue —
    # it must never block monthly report delivery, only surface as a warning.
    assert snapshot.delivery_blockers == ()
    assert any("약정 콘텐츠 12편 중 2편" in warning for warning in snapshot.delivery_warnings)
    assert any("필수 사후검수 샘플 1건" in warning for warning in snapshot.delivery_warnings)


def test_pending_review_sample_no_longer_includes_remediated_items():
    cutoff = datetime(2026, 8, 1, 0, 15, tzinfo=timezone.utc)
    reviewed = _item(reviewed_at=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc))
    # Remediated-but-not-edited, non-seq-1 items are excluded from the sample entirely now.
    remediated_only = _item(sequence_no=5)

    snapshot = build_monthly_content_operations_snapshot(
        plan="PLAN_12",
        scheduled_items=[reviewed, remediated_only],
        published_items=[reviewed, remediated_only],
        cutoff_at=cutoff,
    )

    review = snapshot.payload["post_publish_review"]
    assert review["required_sample_count"] == 1
    assert review["pending_count"] == 0
    assert snapshot.delivery_blockers == ()
    assert not any("사후검수" in warning for warning in snapshot.delivery_warnings)


def test_closed_month_shortfall_is_reported_without_making_delivery_impossible():
    cutoff = datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)
    reviewed = _item(reviewed_at=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc))

    snapshot = build_monthly_content_operations_snapshot(
        plan="PLAN_12",
        scheduled_items=[reviewed],
        published_items=[reviewed],
        cutoff_at=cutoff,
    )

    assert snapshot.delivery_blockers == ()
    assert snapshot.delivery_warnings == ("약정 콘텐츠 12편 중 1편만 발행되었습니다.",)
    assert snapshot.payload["delivery_warnings"] == list(snapshot.delivery_warnings)
    assert "운영 경고" in snapshot.payload["operator_copy"]["next_action"]


def test_monthly_content_operations_counts_body_edits_after_publication_as_review_sample():
    cutoff = datetime(2026, 8, 1, 0, 15, tzinfo=timezone.utc)
    edited = _item(
        sequence_no=4,
        reviewed_at=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc),
        body_updated_at=datetime(2026, 7, 11, 1, 0, tzinfo=timezone.utc),
    )

    snapshot = build_monthly_content_operations_snapshot(
        plan="PLAN_12",
        scheduled_items=[edited],
        published_items=[edited],
        cutoff_at=cutoff,
    )

    assert snapshot.payload["post_publish_review"]["required_sample_count"] == 1
    assert snapshot.payload["post_publish_review"]["reviewed_count"] == 1
    assert snapshot.payload["post_publish_review"]["pending_count"] == 0


def test_monthly_content_operations_uses_rebuild_cutoff_for_late_review_completion():
    cutoff = datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)
    late_reviewed = _item(
        reviewed_at=datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc),
    )

    snapshot = build_monthly_content_operations_snapshot(
        plan="PLAN_12",
        scheduled_items=[late_reviewed],
        published_items=[late_reviewed],
        cutoff_at=cutoff,
    )

    assert snapshot.payload["post_publish_review"]["reviewed_count"] == 1
    assert snapshot.payload["post_publish_review"]["pending_count"] == 0
    assert not any("사후검수" in blocker for blocker in snapshot.delivery_blockers)
    assert not any("사후검수" in warning for warning in snapshot.delivery_warnings)


def test_monthly_content_operations_distinguishes_pending_from_overdue():
    cutoff = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    fresh_pending = _item()

    snapshot = build_monthly_content_operations_snapshot(
        plan="PLAN_12",
        scheduled_items=[fresh_pending],
        published_items=[fresh_pending],
        cutoff_at=cutoff,
    )

    assert snapshot.payload["post_publish_review"]["pending_count"] == 1
    assert snapshot.payload["post_publish_review"]["overdue_count"] == 0
