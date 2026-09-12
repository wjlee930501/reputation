"""Mark a cover image that was reused from another published article.

Additive only. Existing rows keep NULL, which means "this image was generated for
this article" — the certification shape those rows already satisfy. Readers that
predate this column simply never see a reused image, so the rollout is safe in
either order.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0074_add_image_reuse_marker"
down_revision = "0073_add_director_deltas"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "content_items",
        sa.Column("image_reused_from_content_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_content_items_image_reused_from",
        "content_items",
        "content_items",
        ["image_reused_from_content_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint(
        "fk_content_items_image_reused_from", "content_items", type_="foreignkey"
    )
    op.drop_column("content_items", "image_reused_from_content_id")
