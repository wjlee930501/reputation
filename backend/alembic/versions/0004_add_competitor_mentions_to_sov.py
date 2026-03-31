"""add competitor_mentions to sov_records

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.add_column(
        "sov_records",
        sa.Column("competitor_mentions", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sov_records", "competitor_mentions")
