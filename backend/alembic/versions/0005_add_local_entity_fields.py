"""add local entity fields to hospitals

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hospitals", sa.Column("google_business_profile_url", sa.String(500), nullable=True))
    op.add_column("hospitals", sa.Column("google_maps_url", sa.String(500), nullable=True))
    op.add_column("hospitals", sa.Column("naver_place_url", sa.String(500), nullable=True))
    op.add_column("hospitals", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("hospitals", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("hospitals", "longitude")
    op.drop_column("hospitals", "latitude")
    op.drop_column("hospitals", "naver_place_url")
    op.drop_column("hospitals", "google_maps_url")
    op.drop_column("hospitals", "google_business_profile_url")
