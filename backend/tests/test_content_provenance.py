from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.models.content import ContentStatus
from app.services.content_provenance import (
    generation_input_source_ids,
    mark_removed_source_dependency,
    removed_generation_source_ids,
)
from app.services.knowledge_changes import (
    future_addendum_metadata,
    invalidate_article_authority,
    preserve_source_version_authority,
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
    item.active_revision_id = uuid4()
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
    assert item.active_revision_id is None


def test_exact_provenance_is_generation_input_not_semantic_claim_dependency() -> None:
    source_id = str(uuid4())
    item = _item([source_id])
    item.essence_check_summary["authority_change"] = {
        "source_ids": [],
        "legacy_authority_marker": "preserve",
    }

    assert generation_input_source_ids(item) == (source_id,)

    item.status = ContentStatus.PUBLISHED
    item.active_revision_id = uuid4()
    item.first_published_at = datetime.now(timezone.utc)
    item.first_published_by = "ae@example.com"
    item.published_at = item.first_published_at
    item.published_by = item.first_published_by
    item.scheduled_date = date(2026, 10, 1)
    item.carried_over_from = None
    item.content_revision = 3
    item.generation_claim_token = uuid4()
    item.generation_claimed_at = datetime.now(timezone.utc)
    base = SimpleNamespace(source_asset_ids=[source_id])

    assert invalidate_article_authority(
        item,
        source_id=source_id,
        base=base,
        reason="SOURCE_RETRACTED",
        now=datetime.now(timezone.utc),
    )
    marker = item.essence_check_summary["authority_change"]
    assert marker["dependency_certainty"] == "EXACT_INPUT"
    assert marker["dependency_scope"] == "GENERATION_INPUT"
    assert marker["semantic_claim_dependency"] is False
    assert marker["legacy_authority_marker"] == "preserve"
    assert item.active_revision_id is None
    assert item.first_published_by == "ae@example.com"


def test_malformed_provenance_is_unknown_legacy_not_exact() -> None:
    source_id = str(uuid4())
    for malformed in (
        {"evidence_source_asset_ids": "not-a-list"},
        {"evidence_source_asset_ids": ["not-a-uuid"]},
        {"evidence_source_asset_ids": [source_id, {"source_id": source_id}]},
    ):
        item = SimpleNamespace(essence_check_summary={"generation_provenance": malformed})
        assert generation_input_source_ids(item) is None


def test_client_metadata_cannot_inject_or_replace_source_version_authority() -> None:
    source_id = str(uuid4())
    authority = {
        "mode": "FUTURE_ONLY_ADDENDUM",
        "root_source_id": source_id,
        "previous_source_id": source_id,
        "version": 2,
    }
    requested = {
        "label": "operator value",
        "_source_version": {"mode": "CORRECTION", "version": 999},
    }

    assert preserve_source_version_authority({}, requested) == {"label": "operator value"}
    assert preserve_source_version_authority(
        {"_source_version": authority}, requested
    ) == {"label": "operator value", "_source_version": authority}

    child_id = uuid4()
    child = SimpleNamespace(
        id=child_id,
        source_metadata={"_source_version": authority},
    )
    next_metadata = future_addendum_metadata(child, requested)
    assert next_metadata["_source_version"] == {
        "mode": "FUTURE_ONLY_ADDENDUM",
        "root_source_id": source_id,
        "previous_source_id": str(child_id),
        "version": 3,
    }
