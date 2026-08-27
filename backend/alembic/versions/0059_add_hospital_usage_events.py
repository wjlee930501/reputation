"""Add an observational hospital usage ledger for provider call counts and tokens.

This append-only ledger keeps hospital attribution separate from global cost guards;
it intentionally stores no prices or billing estimates.

Revision ID: 0059_add_hospital_usage_events
Revises: 0058_repair_monthly_delivery_drift
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0059_add_hospital_usage_events"
down_revision: str | None = "0058_repair_monthly_delivery_drift"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hospital_usage_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("hospital_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('onboarding', 'content', 'image', 'sov')",
            name="ck_hospital_usage_events_kind",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hospital_usage_events_hospital_id",
        "hospital_usage_events",
        ["hospital_id"],
    )
    op.create_index(
        "ix_hospital_usage_events_hospital_kind_created",
        "hospital_usage_events",
        ["hospital_id", "kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hospital_usage_events_hospital_kind_created",
        table_name="hospital_usage_events",
    )
    op.drop_index("ix_hospital_usage_events_hospital_id", table_name="hospital_usage_events")
    op.drop_table("hospital_usage_events")
