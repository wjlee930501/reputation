"""Track semantic policy approval for generated content images.

Revision ID: 0053_add_content_image_policy_verification
Revises: 0052_add_photo_asset_provenance
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0053_add_content_image_policy_verification"
down_revision: str | None = "0052_add_photo_asset_provenance"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column("image_policy_verified_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("content_items", "image_policy_verified_at")
