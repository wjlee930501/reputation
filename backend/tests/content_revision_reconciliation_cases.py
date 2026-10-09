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


def test_historical_snapshot_preserves_the_pre_migration_public_subset(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        snapshotted_ids = set(
            connection.execute(
                text(
                    "SELECT DISTINCT content_item_id FROM content_revisions"
                )
            ).scalars()
        )

    assert snapshotted_ids == {VISIBLE_ID, MALFORMED_ID, HASH_ONLY_ID}


@pytest.mark.parametrize("content_id", [MALFORMED_ID, HASH_ONLY_ID])
def test_previously_published_text_gets_a_truthful_historical_snapshot(
    migrated_engine,
    content_id: uuid.UUID,
) -> None:
    with migrated_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT ci.active_revision_id, cr.approval_status, cr.title, cr.body, "
                "cr.source_snapshot, cr.generation_provenance, cr.source_snapshot_hash, "
                "cr.source_fingerprint FROM content_items ci "
                "LEFT JOIN content_revisions cr ON cr.id=ci.active_revision_id "
                "WHERE ci.id=:id"
            ),
            {"id": content_id},
        ).one()
        assert row.active_revision_id is not None
        assert row.approval_status == "HISTORICAL_PUBLICATION"
        assert row.title in {"오도된 근거", "해시만 근거"}
        assert row.body in {"오도 본문", "해시 본문"}
        assert row.generation_provenance is None
        assert row.source_snapshot_hash is None
        assert row.source_fingerprint is None
        assert row.source_snapshot is None
        revision_count = connection.execute(
            text("SELECT count(*) FROM content_revisions WHERE content_item_id=:id"),
            {"id": content_id},
        ).scalar_one()
        assert revision_count == 1


def test_reconciliation_preserves_frozen_history_until_explicit_unpublish(migrated_engine) -> None:
    with migrated_engine.begin() as connection:
        historical_id = connection.execute(
            text("SELECT active_revision_id FROM content_items WHERE id=:id"),
            {"id": MALFORMED_ID},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE content_items edited SET "
                "title='승인되지 않은 편집', body='승인되지 않은 본문', content_revision=8, "
                "content_brief=source.content_brief, "
                "essence_check_summary=source.essence_check_summary, "
                "references_list=source.references_list, reference_checks=source.reference_checks "
                "FROM content_items source WHERE edited.id=:id AND source.id=:source_id"
            ),
            {"id": MALFORMED_ID, "source_id": VISIBLE_ID},
        )
        assert connection.execute(
            text("SELECT * FROM reconcile_content_revisions(:id)"),
            {"id": MALFORMED_ID},
        ).one() == (0, 0, 1)
        assert connection.execute(
            text("SELECT active_revision_id FROM content_items WHERE id=:id"),
            {"id": MALFORMED_ID},
        ).scalar_one() == historical_id
        historical = connection.execute(
            text(
                "SELECT approval_status, title, body FROM content_revisions "
                "WHERE id=:id"
            ),
            {"id": historical_id},
        ).one()
        assert historical == ("HISTORICAL_PUBLICATION", "오도된 근거", "오도 본문")

        connection.execute(
            text("UPDATE content_items SET active_revision_id=NULL WHERE id=:id"),
            {"id": MALFORMED_ID},
        )
        assert connection.execute(
            text("SELECT * FROM reconcile_content_revisions(:id)"),
            {"id": MALFORMED_ID},
        ).one() == (1, 0, 0)
        approved = connection.execute(
            text(
                "SELECT cr.id, cr.approval_status, cr.title, cr.body "
                "FROM content_items ci JOIN content_revisions cr "
                "ON cr.id=ci.active_revision_id WHERE ci.id=:id"
            ),
            {"id": MALFORMED_ID},
        ).one()
        assert approved.id != historical_id
        assert (approved.approval_status, approved.title, approved.body) == (
            "APPROVED",
            "승인되지 않은 편집",
            "승인되지 않은 본문",
        )

        connection.execute(
            text("UPDATE content_items SET status='WITHHELD' WHERE id=:id"),
            {"id": MALFORMED_ID},
        )
        assert connection.execute(
            text("SELECT * FROM reconcile_content_revisions(:id)"),
            {"id": MALFORMED_ID},
        ).one() == (0, 1, 0)
