"""Freeze historical publications without inventing modern approval evidence.

Revision ID: 0086_preserve_historical_publications
Revises: 0085_allow_acknowledged_incident_supersession
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "0086_preserve_historical_publications"
down_revision: str | None = "0085_allow_acknowledged_incident_supersession"
branch_labels: str | None = None
depends_on: str | None = None

_RECONCILE_SQL = (
    Path(__file__).resolve().parents[1]
    / "revision_sql"
    / "reconcile_content_revisions_0086.sql"
).read_text(encoding="utf-8")


def upgrade() -> None:
    op.alter_column(
        "content_revisions",
        "approval_status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    for column in (
        "reference_checks",
        "source_snapshot",
        "generation_provenance",
        "source_snapshot_hash",
        "source_fingerprint",
    ):
        op.alter_column("content_revisions", column, nullable=True)
    op.drop_constraint("ck_content_revisions_approval_status", "content_revisions", type_="check")
    op.create_check_constraint(
        "ck_content_revisions_approval_status",
        "content_revisions",
        "approval_status IN ('APPROVED', 'HISTORICAL_PUBLICATION')",
    )
    op.execute(_HISTORICAL_SNAPSHOT_SQL)
    op.execute(_RECONCILE_SQL)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN RAISE EXCEPTION 'cannot downgrade: immutable historical publication "
        "snapshots must be retained'; END $$"
    )


_HISTORICAL_SNAPSHOT_SQL = r"""
WITH candidates AS (
    SELECT
        ci.*,
        coalesce((
            SELECT max(cr.edition_no) + 1
            FROM content_revisions cr
            WHERE cr.content_item_id = ci.id
        ), 1) AS next_edition
    FROM content_items ci
    WHERE ci.status::text = 'PUBLISHED'
      AND ci.active_revision_id IS NULL
      AND ci.published_at IS NOT NULL
      AND ci.content_philosophy_id IS NOT NULL
      AND ci.content_revision > 0
      AND btrim(coalesce(ci.title, '')) <> ''
      AND btrim(coalesce(ci.body, '')) <> ''
      AND jsonb_typeof(ci.references_list) = 'array'
), inserted AS (
    INSERT INTO content_revisions (
        id, content_item_id, edition_no, legacy_content_revision, title, body, meta_description,
        faq_question, faq_answer_summary, references_list, reference_checks,
        generation_philosophy_id, last_reviewed_philosophy_id, generated_at, reviewed_at,
        reviewed_by, source_snapshot, generation_provenance, source_snapshot_hash,
        source_fingerprint, approval_hash, approval_status, approved_at, approved_by
    )
    SELECT
        (
            substr(md5(c.id::text || ':historical:' || c.next_edition::text), 1, 8) || '-' ||
            substr(md5(c.id::text || ':historical:' || c.next_edition::text), 9, 4) || '-' ||
            substr(md5(c.id::text || ':historical:' || c.next_edition::text), 13, 4) || '-' ||
            substr(md5(c.id::text || ':historical:' || c.next_edition::text), 17, 4) || '-' ||
            substr(md5(c.id::text || ':historical:' || c.next_edition::text), 21, 12)
        )::uuid,
        c.id, c.next_edition, c.content_revision, c.title, c.body, c.meta_description,
        c.faq_question, c.faq_answer_summary, c.references_list,
        NULL,
        c.generation_philosophy_id, c.last_reviewed_philosophy_id, c.generated_at,
        c.post_publish_reviewed_at, c.post_publish_reviewed_by,
        NULL,
        NULL,
        NULL,
        NULL,
        encode(digest(convert_to(concat_ws('|', c.title, c.body,
            coalesce(c.meta_description, ''), coalesce(c.faq_question, ''),
            coalesce(c.faq_answer_summary, ''), c.references_list::text), 'UTF8'), 'sha256'), 'hex'),
        'HISTORICAL_PUBLICATION', c.published_at, c.published_by
    FROM candidates c
    RETURNING id, content_item_id
)
UPDATE content_items ci
SET active_revision_id = inserted.id
FROM inserted
WHERE ci.id = inserted.content_item_id
  AND ci.active_revision_id IS NULL;
"""
