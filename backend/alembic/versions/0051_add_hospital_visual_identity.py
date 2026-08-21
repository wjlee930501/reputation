"""Add operator-approved visual identity fields for public clinic surfaces.

Revision ID: 0051_add_hospital_visual_identity
Revises: 0050_add_domain_cert_job_identity
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0051_add_hospital_visual_identity"
down_revision: str | None = "0050_add_domain_cert_job_identity"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hospitals", sa.Column("hero_media_kind", sa.String(length=32), nullable=True))
    op.add_column("hospitals", sa.Column("hero_headline", sa.String(length=160), nullable=True))
    op.add_column("hospitals", sa.Column("hero_description", sa.String(length=320), nullable=True))
    op.add_column("hospitals", sa.Column("image_style_direction", sa.String(length=600), nullable=True))
    op.add_column("hospitals", sa.Column("site_access_mode", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("hospitals", "site_access_mode")
    op.drop_column("hospitals", "image_style_direction")
    op.drop_column("hospitals", "hero_description")
    op.drop_column("hospitals", "hero_headline")
    op.drop_column("hospitals", "hero_media_kind")
