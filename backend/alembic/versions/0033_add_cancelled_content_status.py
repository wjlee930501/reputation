"""add terminal cancelled content status

Revision ID: 0033_add_cancelled_content_status
Revises: 0032_add_post_publish_review
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0033_add_cancelled_content_status"
down_revision: str | None = "0032_add_post_publish_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL enum additions are additive and safe for a rolling deployment.  The
    # value is terminal: workers only select DRAFT/REJECTED, so cancelled backlog
    # cannot be regenerated or published by a later scheduler run.
    op.execute("ALTER TYPE contentstatus ADD VALUE IF NOT EXISTS 'CANCELLED'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value without rebuilding the type.  Keeping the
    # unused additive value is safer than rewriting a live content table on rollback.
    pass
