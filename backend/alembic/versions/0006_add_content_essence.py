"""add content essence models

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE hospital_source_type AS ENUM ('NAVER_BLOG', 'YOUTUBE', 'HOMEPAGE', 'INTERVIEW', 'LANDING_PAGE', 'BROCHURE', 'INTERNAL_NOTE', 'OTHER');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE hospital_source_status AS ENUM ('PENDING', 'PROCESSED', 'EXCLUDED', 'ERROR');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE hospital_evidence_note_type AS ENUM ('KEY_MESSAGE', 'TONE_SIGNAL', 'TREATMENT_SIGNAL', 'RISK_SIGNAL', 'PATIENT_PROMISE', 'DOCTOR_PHILOSOPHY', 'LOCAL_CONTEXT', 'PROOF_POINT', 'CONFLICT');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE hospital_philosophy_status AS ENUM ('DRAFT', 'APPROVED', 'ARCHIVED');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    source_type = postgresql.ENUM(
        "NAVER_BLOG",
        "YOUTUBE",
        "HOMEPAGE",
        "INTERVIEW",
        "LANDING_PAGE",
        "BROCHURE",
        "INTERNAL_NOTE",
        "OTHER",
        name="hospital_source_type",
        create_type=False,
    )
    source_status = postgresql.ENUM(
        "PENDING", "PROCESSED", "EXCLUDED", "ERROR", name="hospital_source_status", create_type=False
    )
    evidence_note_type = postgresql.ENUM(
        "KEY_MESSAGE",
        "TONE_SIGNAL",
        "TREATMENT_SIGNAL",
        "RISK_SIGNAL",
        "PATIENT_PROMISE",
        "DOCTOR_PHILOSOPHY",
        "LOCAL_CONTEXT",
        "PROOF_POINT",
        "CONFLICT",
        name="hospital_evidence_note_type",
        create_type=False,
    )
    philosophy_status = postgresql.ENUM(
        "DRAFT", "APPROVED", "ARCHIVED", name="hospital_philosophy_status", create_type=False
    )

    op.create_table(
        "hospital_source_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hospital_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("url", sa.String(1000), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("operator_note", sa.Text(), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("status", source_status, nullable=False, server_default="PENDING"),
        sa.Column("process_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_hospital_source_assets_hospital_status",
        "hospital_source_assets",
        ["hospital_id", "status"],
    )
    op.create_index(
        "ix_hospital_source_assets_hospital_type",
        "hospital_source_assets",
        ["hospital_id", "source_type"],
    )
    op.create_index(
        "ix_hospital_source_assets_hospital_hash",
        "hospital_source_assets",
        ["hospital_id", "content_hash"],
    )

    op.create_table(
        "hospital_source_evidence_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hospital_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hospital_source_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("note_type", evidence_note_type, nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("excerpt_start", sa.Integer(), nullable=True),
        sa.Column("excerpt_end", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("note_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_hospital_source_evidence_notes_hospital_type",
        "hospital_source_evidence_notes",
        ["hospital_id", "note_type"],
    )
    op.create_index(
        "ix_hospital_source_evidence_notes_source_asset",
        "hospital_source_evidence_notes",
        ["source_asset_id"],
    )

    op.create_table(
        "hospital_content_philosophies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hospital_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", philosophy_status, nullable=False, server_default="DRAFT"),
        sa.Column("positioning_statement", sa.Text(), nullable=True),
        sa.Column("doctor_voice", sa.Text(), nullable=True),
        sa.Column("patient_promise", sa.Text(), nullable=True),
        sa.Column("content_principles", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tone_guidelines", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("must_use_messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("avoid_messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("treatment_narratives", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("local_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("medical_ad_risk_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_map", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_asset_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("unsupported_gaps", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("conflict_notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("synthesis_notes", sa.Text(), nullable=True),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_hospital_content_philosophies_hospital_status",
        "hospital_content_philosophies",
        ["hospital_id", "status"],
    )
    op.create_index(
        "ix_hospital_content_philosophies_hospital_version",
        "hospital_content_philosophies",
        ["hospital_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_hospital_content_philosophies_one_approved",
        "hospital_content_philosophies",
        ["hospital_id"],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED'"),
    )

    op.add_column(
        "content_items",
        sa.Column(
            "content_philosophy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hospital_content_philosophies.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("content_items", sa.Column("essence_status", sa.String(50), nullable=True))
    op.add_column("content_items", sa.Column("essence_check_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_index("ix_content_items_essence_status", "content_items", ["essence_status"])
    op.create_index("ix_content_items_content_philosophy_id", "content_items", ["content_philosophy_id"])

    op.add_column("monthly_reports", sa.Column("essence_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("monthly_reports", "essence_summary")
    op.drop_index("ix_content_items_content_philosophy_id", table_name="content_items")
    op.drop_index("ix_content_items_essence_status", table_name="content_items")
    op.drop_column("content_items", "essence_check_summary")
    op.drop_column("content_items", "essence_status")
    op.drop_column("content_items", "content_philosophy_id")

    op.drop_index("uq_hospital_content_philosophies_one_approved", table_name="hospital_content_philosophies")
    op.drop_index("ix_hospital_content_philosophies_hospital_version", table_name="hospital_content_philosophies")
    op.drop_index("ix_hospital_content_philosophies_hospital_status", table_name="hospital_content_philosophies")
    op.drop_table("hospital_content_philosophies")

    op.drop_index("ix_hospital_source_evidence_notes_source_asset", table_name="hospital_source_evidence_notes")
    op.drop_index("ix_hospital_source_evidence_notes_hospital_type", table_name="hospital_source_evidence_notes")
    op.drop_table("hospital_source_evidence_notes")

    op.drop_index("ix_hospital_source_assets_hospital_hash", table_name="hospital_source_assets")
    op.drop_index("ix_hospital_source_assets_hospital_type", table_name="hospital_source_assets")
    op.drop_index("ix_hospital_source_assets_hospital_status", table_name="hospital_source_assets")
    op.drop_table("hospital_source_assets")

    op.execute("DROP TYPE IF EXISTS hospital_philosophy_status")
    op.execute("DROP TYPE IF EXISTS hospital_evidence_note_type")
    op.execute("DROP TYPE IF EXISTS hospital_source_status")
    op.execute("DROP TYPE IF EXISTS hospital_source_type")
