"""add content generation, review, and image certification contracts

Revision ID: 0066_content_contracts
Revises: 0065_provider_usage
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0066_content_contracts"
down_revision: Union[str, None] = "0065_provider_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column("generation_philosophy_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("last_reviewed_philosophy_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("content_revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "content_items",
        sa.Column("generation_claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("content_items", sa.Column("image_content_hash", sa.String(64), nullable=True))
    op.add_column("content_items", sa.Column("image_subject_hash", sa.String(64), nullable=True))
    op.add_column("content_items", sa.Column("image_policy_version", sa.String(40), nullable=True))
    op.create_foreign_key(
        "fk_content_items_generation_philosophy_id",
        "content_items",
        "hospital_content_philosophies",
        ["generation_philosophy_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_content_items_last_reviewed_philosophy_id",
        "content_items",
        "hospital_content_philosophies",
        ["last_reviewed_philosophy_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # The legacy field represented the most recent successful Essence screen, so
    # it is valid review provenance. It must not be copied into generation provenance:
    # a later relabel may have replaced the original generation basis.
    op.execute(
        "UPDATE content_items "
        "SET last_reviewed_philosophy_id = content_philosophy_id "
        "WHERE content_philosophy_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_content_items_last_reviewed_philosophy_id", "content_items", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_content_items_generation_philosophy_id", "content_items", type_="foreignkey"
    )
    op.drop_column("content_items", "image_policy_version")
    op.drop_column("content_items", "image_subject_hash")
    op.drop_column("content_items", "image_content_hash")
    op.drop_column("content_items", "generation_claim_token")
    op.drop_column("content_items", "content_revision")
    op.drop_column("content_items", "last_reviewed_philosophy_id")
    op.drop_column("content_items", "generation_philosophy_id")
