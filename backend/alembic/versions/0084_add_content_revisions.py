"""Add immutable approved revisions; legacy text stays authoritative during expand."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0084_add_content_revisions"
down_revision: str | None = "0083_add_operation_run_not_before"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "content_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edition_no", sa.Integer(), nullable=False),
        sa.Column("legacy_content_revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("meta_description", sa.String(length=300), nullable=True),
        sa.Column("faq_question", sa.String(length=300), nullable=True),
        sa.Column("faq_answer_summary", sa.String(length=600), nullable=True),
        sa.Column("references_list", postgresql.JSONB(), nullable=False),
        sa.Column("reference_checks", postgresql.JSONB(), nullable=False),
        sa.Column("generation_philosophy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_reviewed_philosophy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("source_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("generation_provenance", postgresql.JSONB(), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("approval_hash", sa.String(length=64), nullable=False),
        sa.Column("approval_status", sa.String(length=20), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("edition_no > 0", name="ck_content_revisions_edition_positive"),
        sa.CheckConstraint(
            "legacy_content_revision > 0", name="ck_content_revisions_legacy_revision_positive"
        ),
        sa.CheckConstraint(
            "approval_status = 'APPROVED'", name="ck_content_revisions_approval_status"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(references_list) = 'array'", name="ck_content_revisions_references_array"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reference_checks) = 'array'",
            name="ck_content_revisions_reference_checks_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_snapshot) = 'object'",
            name="ck_content_revisions_source_snapshot_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(generation_provenance) = 'object'",
            name="ck_content_revisions_generation_provenance_object",
        ),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["generation_philosophy_id"], ["hospital_content_philosophies.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["last_reviewed_philosophy_id"],
            ["hospital_content_philosophies.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "content_item_id", "edition_no", name="uq_content_revisions_item_edition"
        ),
    )
    op.create_index(
        "ix_content_revisions_content_item_id", "content_revisions", ["content_item_id"]
    )
    op.add_column(
        "content_items",
        sa.Column("active_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("pending_revision", postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        "ck_content_items_pending_revision_object",
        "content_items",
        "pending_revision IS NULL OR jsonb_typeof(pending_revision) = 'object'",
    )
    op.create_foreign_key(
        "fk_content_items_active_revision_id",
        "content_items",
        "content_revisions",
        ["active_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_content_items_active_revision_id", "content_items", ["active_revision_id"])
    op.execute(_RECONCILE_FUNCTION_SQL)
    op.execute("SELECT * FROM reconcile_content_revisions()")
    op.execute(_IMMUTABLE_FUNCTION_SQL)
    op.execute(_IMMUTABLE_TRIGGER_SQL)
    op.execute(_ACTIVE_OWNER_FUNCTION_SQL)
    op.execute(_ACTIVE_OWNER_TRIGGER_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_content_items_active_revision_owner ON content_items")
    op.execute("DROP FUNCTION IF EXISTS enforce_content_active_revision_owner()")
    op.execute("DROP TRIGGER IF EXISTS trg_content_revisions_immutable ON content_revisions")
    op.execute("DROP FUNCTION IF EXISTS reject_content_revision_mutation()")
    op.execute("DROP FUNCTION IF EXISTS reconcile_content_revisions(uuid)")
    op.drop_index("ix_content_items_active_revision_id", table_name="content_items")
    op.drop_constraint("fk_content_items_active_revision_id", "content_items", type_="foreignkey")
    op.drop_constraint("ck_content_items_pending_revision_object", "content_items", type_="check")
    op.drop_column("content_items", "pending_revision")
    op.drop_column("content_items", "active_revision_id")
    op.drop_index("ix_content_revisions_content_item_id", table_name="content_revisions")
    op.drop_table("content_revisions")


_RECONCILE_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION reconcile_content_revisions(target_content_id uuid DEFAULT NULL)
RETURNS TABLE(created_count integer, cleared_count integer, unchanged_count integer)
LANGUAGE plpgsql AS $$
DECLARE
    item record; active record; new_revision_id uuid; next_edition integer; eligible boolean;
BEGIN
    created_count := 0; cleared_count := 0; unchanged_count := 0;
    FOR item IN
        SELECT ci.*, p.source_snapshot_hash AS approved_source_snapshot_hash
        FROM content_items ci
        LEFT JOIN hospital_content_philosophies p
          ON p.id = ci.content_philosophy_id
         AND p.hospital_id = ci.hospital_id
         AND p.status = 'APPROVED'
        WHERE target_content_id IS NULL OR ci.id = target_content_id
        ORDER BY ci.id
        FOR UPDATE OF ci
    LOOP
        eligible := item.status = 'PUBLISHED'
            AND item.published_at IS NOT NULL
            AND btrim(coalesce(item.title, '')) <> ''
            AND btrim(coalesce(item.body, '')) <> ''
            AND item.essence_status = 'ALIGNED'
            AND item.content_philosophy_id IS NOT NULL
            AND item.approved_source_snapshot_hash IS NOT NULL
            AND jsonb_typeof(item.content_brief) = 'object'
            AND btrim(coalesce(item.content_brief ->> 'schema_version', '')) <> ''
            AND btrim(coalesce(item.content_brief ->> 'target_query', '')) <> ''
            AND jsonb_typeof(item.content_brief -> 'treatment_narrative') = 'object'
            AND item.content_brief -> 'treatment_narrative' ->> 'source' IN ('approved_philosophy', 'hospital_profile')
            AND btrim(coalesce(item.content_brief -> 'treatment_narrative' ->> 'angle', '')) <> ''
            AND jsonb_typeof(item.content_brief -> 'source_snapshot') = 'object'
            AND btrim(coalesce(item.content_brief -> 'source_snapshot' ->> 'hash', '')) <> ''
            AND jsonb_typeof(item.content_brief -> 'source_snapshot' -> 'source_asset_ids') = 'array'
            AND jsonb_path_exists(item.content_brief, '$.source_snapshot.source_asset_ids[*]')
            AND jsonb_typeof(item.essence_check_summary -> 'generation_provenance') = 'object'
            AND (jsonb_path_exists(item.essence_check_summary, '$.generation_provenance.evidence_note_ids[*]')
                 OR jsonb_path_exists(item.essence_check_summary, '$.generation_provenance.source_asset_ids[*]')
                 OR jsonb_path_exists(item.essence_check_summary, '$.generation_provenance.evidence_source_asset_ids[*]'))
            AND jsonb_typeof(item.references_list) = 'array'
            AND jsonb_typeof(item.reference_checks) = 'array'
            AND ((jsonb_array_length(item.references_list) = 0
                  AND jsonb_array_length(item.reference_checks) = 0)
                 OR (jsonb_array_length(item.references_list) > 0
                     AND jsonb_array_length(item.reference_checks) > 0
                     AND NOT EXISTS (
                         SELECT 1 FROM jsonb_array_elements(item.references_list) ref
                         WHERE btrim(coalesce(ref ->> 'title', '')) = ''
                            OR btrim(coalesce(ref ->> 'url', '')) = ''
                            OR NOT EXISTS (
                                SELECT 1 FROM jsonb_array_elements(item.reference_checks) check_row
                                WHERE lower(coalesce(check_row ->> 'verdict', '')) = 'pass'
                                  AND btrim(coalesce(check_row ->> 'url', '')) = btrim(ref ->> 'url')
                            )
                     )))
            AND NOT coalesce((item.essence_check_summary ->> 'blocking')::boolean, false)
            AND NOT coalesce((item.essence_check_summary ->> 'authority_change')::boolean, false)
            AND NOT coalesce(item.essence_check_summary -> 'ai_review' ->> 'status' = 'UNAVAILABLE', false)
            AND NOT coalesce(
                item.essence_check_summary -> 'ai_review' ->> 'status' = 'REVISE'
                AND coalesce((item.essence_check_summary -> 'ai_review' ->> 'blocking')::boolean, true), false
            )
            AND (item.content_type::text = 'NOTICE' OR (
                jsonb_typeof(item.references_list) = 'array' AND jsonb_array_length(item.references_list) > 0
            ))
            AND (item.content_type::text <> 'FAQ' OR (
                right(btrim(coalesce(item.faq_question, '')), 1) = '?'
                AND btrim(coalesce(item.faq_answer_summary, '')) <> ''
            ));

        IF NOT eligible THEN
            IF item.active_revision_id IS NOT NULL THEN
                UPDATE content_items
                SET active_revision_id = NULL
                WHERE id = item.id
                  AND content_revision = item.content_revision
                  AND active_revision_id = item.active_revision_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'content revision reconciliation CAS failed for %', item.id;
                END IF;
                cleared_count := cleared_count + 1;
            ELSE
                unchanged_count := unchanged_count + 1;
            END IF;
            CONTINUE;
        END IF;

        SELECT * INTO active FROM content_revisions WHERE id = item.active_revision_id;
        IF FOUND
           AND active.content_item_id = item.id
           AND active.legacy_content_revision = item.content_revision
           AND active.title = item.title
           AND active.body = item.body
           AND active.meta_description IS NOT DISTINCT FROM item.meta_description
           AND active.faq_question IS NOT DISTINCT FROM item.faq_question
           AND active.faq_answer_summary IS NOT DISTINCT FROM item.faq_answer_summary
           AND active.references_list IS NOT DISTINCT FROM item.references_list
           AND active.reference_checks IS NOT DISTINCT FROM item.reference_checks
        THEN
            unchanged_count := unchanged_count + 1;
            CONTINUE;
        END IF;

        SELECT coalesce(max(edition_no), 0) + 1 INTO next_edition
        FROM content_revisions WHERE content_item_id = item.id;
        new_revision_id := (
            substr(md5(item.id::text || ':' || next_edition::text), 1, 8) || '-' || substr(md5(item.id::text || ':' || next_edition::text), 9, 4) || '-' ||
            substr(md5(item.id::text || ':' || next_edition::text), 13, 4) || '-' || substr(md5(item.id::text || ':' || next_edition::text), 17, 4) || '-' ||
            substr(md5(item.id::text || ':' || next_edition::text), 21, 12)
        )::uuid;
        INSERT INTO content_revisions (
            id, content_item_id, edition_no, legacy_content_revision, title, body, meta_description,
            faq_question, faq_answer_summary, references_list, reference_checks,
            generation_philosophy_id, last_reviewed_philosophy_id, generated_at, reviewed_at,
            reviewed_by, source_snapshot, generation_provenance, source_snapshot_hash,
            source_fingerprint, approval_hash, approval_status, approved_at, approved_by
        ) VALUES (
            new_revision_id, item.id, next_edition, item.content_revision, item.title, item.body,
            item.meta_description, item.faq_question, item.faq_answer_summary, item.references_list,
            item.reference_checks, item.generation_philosophy_id, item.last_reviewed_philosophy_id,
            item.generated_at, item.post_publish_reviewed_at, item.post_publish_reviewed_by,
            item.content_brief,
            item.essence_check_summary -> 'generation_provenance',
            item.approved_source_snapshot_hash,
            encode(digest(convert_to(concat_ws('|',
                coalesce(item.content_brief::text, ''),
                coalesce((item.essence_check_summary -> 'generation_provenance')::text, ''),
                coalesce(item.reference_checks::text, '')), 'UTF8'), 'sha256'), 'hex'),
            encode(digest(convert_to(concat_ws('|', item.title, item.body,
                coalesce(item.meta_description, ''), coalesce(item.faq_question, ''),
                coalesce(item.faq_answer_summary, ''),
                coalesce(item.references_list::text, '')), 'UTF8'), 'sha256'), 'hex'),
            'APPROVED', item.published_at, item.published_by
        );
        UPDATE content_items
        SET active_revision_id = new_revision_id
        WHERE id = item.id AND content_revision = item.content_revision;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'content revision reconciliation CAS failed for %', item.id;
        END IF;
        created_count := created_count + 1;
    END LOOP;
    RETURN NEXT;
END;
$$;
"""


_IMMUTABLE_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION reject_content_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'content revisions are immutable';
END;
$$;
"""


_IMMUTABLE_TRIGGER_SQL = r"""
CREATE TRIGGER trg_content_revisions_immutable
BEFORE UPDATE OR DELETE ON content_revisions
FOR EACH ROW EXECUTE FUNCTION reject_content_revision_mutation();
"""


_ACTIVE_OWNER_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION enforce_content_active_revision_owner()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.active_revision_id IS NULL OR (TG_OP = 'UPDATE'
       AND NEW.active_revision_id IS NOT DISTINCT FROM OLD.active_revision_id) THEN
        RETURN NEW;
    END IF;
    PERFORM 1 FROM content_revisions
    WHERE id = NEW.active_revision_id AND content_item_id = NEW.id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'active revision does not belong to content item %', NEW.id;
    END IF;
    RETURN NEW;
END;
$$;
"""


_ACTIVE_OWNER_TRIGGER_SQL = r"""
CREATE TRIGGER trg_content_items_active_revision_owner
BEFORE INSERT OR UPDATE ON content_items
FOR EACH ROW EXECUTE FUNCTION enforce_content_active_revision_owner();
"""
