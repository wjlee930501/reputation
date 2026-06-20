"""add content generation claims

Revision ID: 0029_add_content_generation_claims
Revises: 0028_release_blocker_integrity_constraints
Create Date: 2026-06-20 20:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029_add_content_generation_claims"
down_revision: Union[str, None] = "0028_release_blocker_integrity_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column("generation_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_content_items_generation_claimed_at",
        "content_items",
        ["generation_claimed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_items_generation_claimed_at", table_name="content_items")
    op.drop_column("content_items", "generation_claimed_at")
