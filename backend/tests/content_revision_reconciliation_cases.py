"""Adversarial cases imported by the 0084 migration acceptance module."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

VISIBLE_ID, HIDDEN_ID, WITHHELD_ID, UNKNOWN_ID, MALFORMED_ID, HASH_ONLY_ID = (
    uuid.UUID(f"84000000-0000-0000-0000-{value:012d}") for value in range(1, 7)
)


def test_post_drain_reconciliation_handles_five_legacy_commit_classes_repeatably(
    migrated_engine,
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            text("UPDATE content_items SET title='편집된 공개판', content_revision=8 WHERE id=:id"),
            {"id": VISIBLE_ID},
        )
        assert connection.execute(text("SELECT * FROM reconcile_content_revisions()" )).one() == (
            1,
            0,
            5,
        )
        connection.execute(
            text("UPDATE content_items SET status='REJECTED', content_revision=9 WHERE id=:id"),
            {"id": VISIBLE_ID},
        )
        assert connection.execute(text("SELECT * FROM reconcile_content_revisions()" )).one()[1] == 1
        connection.execute(
            text(
                "UPDATE content_items SET status='PUBLISHED', body='재발행 본문', "
                "published_at=now(), content_revision=10 WHERE id=:id"
            ),
            {"id": VISIBLE_ID},
        )
        assert connection.execute(text("SELECT * FROM reconcile_content_revisions()" )).one()[0] == 1
        connection.execute(
            text("UPDATE content_items SET status='WITHHELD', content_revision=11 WHERE id=:id"),
            {"id": VISIBLE_ID},
        )
        assert connection.execute(text("SELECT * FROM reconcile_content_revisions()" )).one()[1] == 1
        connection.execute(
            text(
                "UPDATE content_items SET status='DRAFT', body='재생성 비공개 본문', "
                "content_revision=12 WHERE id=:id"
            ),
            {"id": VISIBLE_ID},
        )
        assert connection.execute(text("SELECT * FROM reconcile_content_revisions()" )).one()[0] == 0
        count_before = connection.execute(
            text("SELECT count(*) FROM content_revisions WHERE content_item_id=:id"),
            {"id": VISIBLE_ID},
        ).scalar_one()
        assert count_before == 3
        assert connection.execute(text("SELECT * FROM reconcile_content_revisions()" )).one()[0] == 0
        count_after = connection.execute(
            text("SELECT count(*) FROM content_revisions WHERE content_item_id=:id"),
            {"id": VISIBLE_ID},
        ).scalar_one()
        assert count_after == count_before


def test_approved_revision_rows_reject_in_place_mutation(migrated_engine) -> None:
    with pytest.raises(DBAPIError, match="content revisions are immutable"):
        with migrated_engine.begin() as connection:
            connection.execute(
                text("UPDATE content_revisions SET title='변조' WHERE content_item_id=:id"),
                {"id": VISIBLE_ID},
            )


def test_approved_revision_rows_reject_direct_delete(migrated_engine) -> None:
    with pytest.raises(DBAPIError, match="content revisions are immutable"):
        with migrated_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM content_revisions WHERE content_item_id=:id"),
                {"id": VISIBLE_ID},
            )


def test_content_item_with_approved_history_cannot_be_deleted(migrated_engine) -> None:
    with pytest.raises(DBAPIError):
        with migrated_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM content_items WHERE id=:id"),
                {"id": VISIBLE_ID},
            )


def test_active_pointer_cannot_reference_another_content_item(migrated_engine) -> None:
    with pytest.raises(DBAPIError, match="active revision does not belong"):
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE content_items SET active_revision_id=("
                    "SELECT id FROM content_revisions WHERE content_item_id=:visible_id LIMIT 1"
                    ") WHERE id=:hidden_id"
                ),
                {"visible_id": VISIBLE_ID, "hidden_id": HIDDEN_ID},
            )


def test_unknown_legacy_provenance_is_not_fabricated_or_approved(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT active_revision_id, generation_philosophy_id, "
                "last_reviewed_philosophy_id FROM content_items WHERE id=:id"
            ),
            {"id": UNKNOWN_ID},
        ).one()
        assert row == (None, None, None)
        revision_count = connection.execute(
            text("SELECT count(*) FROM content_revisions WHERE content_item_id=:id"),
            {"id": UNKNOWN_ID},
        ).scalar_one()
        assert revision_count == 0


@pytest.mark.parametrize(
    ("content_id", "expected_brief", "expected_provenance"),
    [
        (MALFORMED_ID, None, None),
        (HASH_ONLY_ID, {"hash": "hash-only", "source_asset_ids": []}, {}),
    ],
)
def test_malformed_or_hash_only_unknown_provenance_is_not_auto_approved(
    migrated_engine,
    content_id: uuid.UUID,
    expected_brief,
    expected_provenance,
) -> None:
    with migrated_engine.connect() as connection:
        row = connection.execute(
            text("SELECT active_revision_id, content_brief, essence_check_summary FROM content_items WHERE id=:id"),
            {"id": content_id},
        ).one()
        assert row.active_revision_id is None
        if expected_brief is None:
            assert row.content_brief is None
        else:
            assert row.content_brief["source_snapshot"] == expected_brief
        if expected_provenance is not None:
            assert row.essence_check_summary["generation_provenance"] == expected_provenance
        revision_count = connection.execute(
            text("SELECT count(*) FROM content_revisions WHERE content_item_id=:id"),
            {"id": content_id},
        ).scalar_one()
        assert revision_count == 0
