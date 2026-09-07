from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.models.content import ContentStatus
from app.services.content_provenance import (
    mark_removed_source_dependency,
    removed_generation_source_ids,
)


def _item(dependencies: list[str]):
    return SimpleNamespace(
        content_philosophy_id=uuid4(),
        last_reviewed_philosophy_id=None,
        essence_status="ALIGNED",
        essence_check_summary={
            "ai_review": {"status": "REVISE", "blocking": True},
            "generation_provenance": {"evidence_source_asset_ids": dependencies},
        },
    )


def test_new_source_does_not_invalidate_unaffected_article() -> None:
    retained = str(uuid4())
    item = _item([retained])
    philosophy = SimpleNamespace(id=uuid4(), source_asset_ids=[retained, str(uuid4())])

    assert removed_generation_source_ids(item, philosophy) == ()
    assert mark_removed_source_dependency(item, philosophy) == ()
    assert item.essence_status == "ALIGNED"


def test_withdrawn_generation_source_preserves_original_and_blocks() -> None:
    removed = str(uuid4())
    original_philosophy_id = uuid4()
    item = _item([removed])
    item.content_philosophy_id = original_philosophy_id
    current = SimpleNamespace(id=uuid4(), source_asset_ids=[])

    assert mark_removed_source_dependency(item, current) == (removed,)
    assert item.content_philosophy_id == original_philosophy_id
    assert item.last_reviewed_philosophy_id == current.id
    assert item.essence_status == "NEEDS_ESSENCE_REVIEW"
    assert item.essence_check_summary["blocking"] is True
    assert item.essence_check_summary["ai_review"]["status"] == "REVISE"


def test_legacy_unknown_provenance_does_not_mass_invalidate() -> None:
    item = SimpleNamespace(
        essence_status="ALIGNED",
        essence_check_summary={"generation_provenance": {"source_asset_ids": [str(uuid4())]}},
    )
    current = SimpleNamespace(id=uuid4(), source_asset_ids=[])

    assert removed_generation_source_ids(item, current) == ()


def test_withdrawn_dependency_unpublishes_and_reschedules_only_affected_article() -> None:
    removed = str(uuid4())
    original_date = date(2026, 1, 15)
    item = _item([removed])
    item.status = ContentStatus.PUBLISHED
    item.scheduled_date = original_date
    item.carried_over_from = None
    item.content_revision = 7
    original_published_at = datetime.now(timezone.utc)
    item.published_at = original_published_at
    item.published_by = "auto"
    item.generation_claimed_at = datetime.now(timezone.utc)
    item.generation_claim_token = uuid4()
    current = SimpleNamespace(id=uuid4(), source_asset_ids=[])

    assert mark_removed_source_dependency(item, current) == (removed,)

    assert item.status == ContentStatus.REJECTED
    assert item.scheduled_date > datetime.now().date()
    assert item.carried_over_from == original_date
    assert item.content_revision == 8
    assert item.published_at == original_published_at
    assert item.essence_check_summary["source_dependency_stale"][
        "original_published_at"
    ] == original_published_at.isoformat()
    assert item.generation_claim_token is None
