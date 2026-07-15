"""add post-publish notification and review tracking

Revision ID: 0032_add_post_publish_review
Revises: 0031_add_hospital_visual_theme
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_add_post_publish_review"
down_revision: str | None = "0031_add_hospital_visual_theme"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column("post_publish_notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("post_publish_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("post_publish_reviewed_by", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("content_items", "post_publish_reviewed_by")
    op.drop_column("content_items", "post_publish_reviewed_at")
    op.drop_column("content_items", "post_publish_notified_at")
