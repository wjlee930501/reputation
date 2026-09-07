"""Add normalized per-provider HTTP-attempt usage events.

Revision ID: 0065_provider_usage
Revises: 0064_manifest_recovery_guard
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0065_provider_usage"
down_revision: str | None = "0064_manifest_recovery_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_usage_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=160)),
        sa.Column("workflow", sa.String(length=80), nullable=False),
        sa.Column("cost_category", sa.String(length=20), nullable=False),
        sa.Column("hospital_id", sa.UUID()),
        sa.Column("lead_id", sa.UUID()),
        sa.Column("run_id", sa.String(length=200)),
        sa.Column("item_id", sa.String(length=200)),
        sa.Column("attempt_id", sa.String(length=200)),
        sa.Column("logical_call_id", sa.String(length=200)),
        sa.Column("http_attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("provider_request_id", sa.String(length=200)),
        sa.Column("idempotency_key", sa.String(length=240)),
        sa.Column("cache_status", sa.String(length=20), server_default="unknown", nullable=False),
        sa.Column("usage_known", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("cache_creation_input_tokens", sa.Integer()),
        sa.Column("cache_read_input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("reasoning_tokens", sa.Integer()),
        sa.Column("search_units", sa.Integer()),
        sa.Column("image_units", sa.Integer()),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "cost_category IN ('content', 'image', 'sov', 'leadgen')",
            name="ck_provider_usage_events_cost_category",
        ),
        sa.CheckConstraint(
            "cache_status IN ('hit', 'miss', 'write', 'bypass', 'unknown')",
            name="ck_provider_usage_events_cache_status",
        ),
        sa.CheckConstraint(
            "hospital_id IS NULL OR lead_id IS NULL",
            name="ck_provider_usage_events_single_owner",
        ),
        sa.CheckConstraint("http_attempt >= 1", name="ck_provider_usage_events_http_attempt"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_provider_usage_events_input_tokens",
        ),
        sa.CheckConstraint(
            "cache_creation_input_tokens IS NULL OR cache_creation_input_tokens >= 0",
            name="ck_provider_usage_events_cache_creation_tokens",
        ),
        sa.CheckConstraint(
            "cache_read_input_tokens IS NULL OR cache_read_input_tokens >= 0",
            name="ck_provider_usage_events_cache_read_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_provider_usage_events_output_tokens",
        ),
        sa.CheckConstraint(
            "reasoning_tokens IS NULL OR reasoning_tokens >= 0",
            name="ck_provider_usage_events_reasoning_tokens",
        ),
        sa.CheckConstraint(
            "search_units IS NULL OR search_units >= 0",
            name="ck_provider_usage_events_search_units",
        ),
        sa.CheckConstraint(
            "image_units IS NULL OR image_units >= 0",
            name="ck_provider_usage_events_image_units",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lead_id"], ["sales_leads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_provider_usage_events_hospital_id", "provider_usage_events", ["hospital_id"]
    )
    op.create_index("ix_provider_usage_events_lead_id", "provider_usage_events", ["lead_id"])
    op.create_index(
        "ix_provider_usage_events_hospital_workflow_created",
        "provider_usage_events", ["hospital_id", "workflow", "created_at"],
    )
    op.create_index(
        "ix_provider_usage_events_lead_workflow_created",
        "provider_usage_events", ["lead_id", "workflow", "created_at"],
    )
    op.create_index(
        "ix_provider_usage_events_run_item_attempt",
        "provider_usage_events", ["run_id", "item_id", "logical_call_id", "http_attempt"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_usage_events_run_item_attempt", table_name="provider_usage_events")
    op.drop_index(
        "ix_provider_usage_events_lead_workflow_created", table_name="provider_usage_events"
    )
    op.drop_index(
        "ix_provider_usage_events_hospital_workflow_created", table_name="provider_usage_events"
    )
    op.drop_index("ix_provider_usage_events_lead_id", table_name="provider_usage_events")
    op.drop_index("ix_provider_usage_events_hospital_id", table_name="provider_usage_events")
    op.drop_table("provider_usage_events")
