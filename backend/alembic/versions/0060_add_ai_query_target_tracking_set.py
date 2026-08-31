"""Mark fixed monthly SoV tracking-set members on existing query targets.

Revision ID: 0060_add_ai_query_target_tracking_set
Revises: 0059_add_hospital_usage_events
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0060_add_ai_query_target_tracking_set"
down_revision: str | None = "0059_add_hospital_usage_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_query_targets",
        sa.Column(
            "in_tracking_set",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ai_query_targets_hospital_tracking_set",
        "ai_query_targets",
        ["hospital_id", "in_tracking_set"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_query_targets_hospital_tracking_set",
        table_name="ai_query_targets",
    )
    op.drop_column("ai_query_targets", "in_tracking_set")
