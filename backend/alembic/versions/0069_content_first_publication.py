"""Preserve immutable first-publication facts for monthly history.

Revision ID: 0069_content_first_publication
Revises: 0068_lead_cost_deferral
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0069_content_first_publication"
down_revision: str | None = "0068_lead_cost_deferral"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("first_published_by", sa.String(length=100), nullable=True),
    )
    # A retained published_at is direct evidence that a publication occurred. Rows whose
    # manual rejection erased that field before this migration cannot be reconstructed
    # truthfully, so they remain NULL rather than receiving an inferred historical date.
    op.execute(
        sa.text(
            """
            UPDATE content_items
               SET first_published_at = published_at,
                   first_published_by = published_by
             WHERE published_at IS NOT NULL
               AND first_published_at IS NULL
            """
        )
    )
    op.create_index(
        "ix_content_items_hospital_first_published",
        "content_items",
        ["hospital_id", "first_published_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_items_hospital_first_published", table_name="content_items")
    op.drop_column("content_items", "first_published_by")
    op.drop_column("content_items", "first_published_at")
