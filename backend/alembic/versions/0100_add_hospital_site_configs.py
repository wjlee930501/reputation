"""Add hospital_site_configs: per-hospital public site section settings (layout editor).

Draft, published and superseded versions of a hospital's section settings. Purely
additive — a hospital without rows keeps rendering from `hospitals.site_layout` exactly
as before (design docs/plans/2026-11-20-clinic-site-sections-design.md, step 3a).

Revision ID: 0100_add_hospital_site_configs
Revises: 0099_add_content_schedule_daily_max_superseded
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0100_add_hospital_site_configs"
down_revision: str | None = "0099_add_content_schedule_daily_max_superseded"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hospital_site_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("layout", sa.String(length=1), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("resolved", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("defaults_version", sa.String(length=40), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("based_on_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_by", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "state IN ('DRAFT', 'PUBLISHED', 'SUPERSEDED')", name="ck_hospital_site_configs_state"
        ),
        sa.CheckConstraint("layout IN ('A', 'B', 'C')", name="ck_hospital_site_configs_layout"),
        sa.CheckConstraint(
            "origin IN ('DEFAULT', 'EDIT', 'RESTORE', 'RESET')",
            name="ck_hospital_site_configs_origin",
        ),
        sa.CheckConstraint(
            "(state = 'DRAFT' AND version IS NULL AND published_at IS NULL) OR "
            "(state <> 'DRAFT' AND version IS NOT NULL AND published_at IS NOT NULL)",
            name="ck_hospital_site_configs_version_state",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["based_on_id"], ["hospital_site_configs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hospital_site_configs_hospital_created",
        "hospital_site_configs",
        ["hospital_id", "created_at"],
    )
    op.create_index(
        "uq_hospital_site_configs_one_draft",
        "hospital_site_configs",
        ["hospital_id"],
        unique=True,
        postgresql_where=sa.text("state = 'DRAFT'"),
    )
    op.create_index(
        "uq_hospital_site_configs_one_published",
        "hospital_site_configs",
        ["hospital_id"],
        unique=True,
        postgresql_where=sa.text("state = 'PUBLISHED'"),
    )
    op.create_index(
        "uq_hospital_site_configs_version",
        "hospital_site_configs",
        ["hospital_id", "version"],
        unique=True,
        postgresql_where=sa.text("version IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_hospital_site_configs_version", table_name="hospital_site_configs")
    op.drop_index("uq_hospital_site_configs_one_published", table_name="hospital_site_configs")
    op.drop_index("uq_hospital_site_configs_one_draft", table_name="hospital_site_configs")
    op.drop_index("ix_hospital_site_configs_hospital_created", table_name="hospital_site_configs")
    op.drop_table("hospital_site_configs")
