"""Record which evidence notes were excluded as noise when an Essence was approved.

Revision ID: 0070_essence_evidence_noise_hash
Revises: 0069_content_first_publication
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0070_essence_evidence_noise_hash"
down_revision: str | None = "0069_content_first_publication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL for approvals made before this column: readiness treats them as matching any
    # noise state (legacy), and the next automatic refresh writes a real value.
    op.add_column(
        "hospital_content_philosophies",
        sa.Column("evidence_noise_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hospital_content_philosophies", "evidence_noise_hash")
