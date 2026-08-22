"""Record the last live HTTPS/CNAME observation for a hospital custom domain.

Revision ID: 0056_add_domain_live_check
Revises: 0055_backfill_photo_asset_kind

Domain badges and the profile tracker were derived only from `domain_cert_*`
columns, which `PATCH /domain` clears whenever the domain or DNS strategy is
re-saved.  A domain that answers HTTPS with a valid certificate and the right
tenant marker therefore kept rendering "저장됨 · DNS 미확인" next to an
onboarding checklist that already called the same domain complete.

These columns hold the outcome of the check that actually proves the domain is
reachable, so the operator can see when it was last observed instead of
inferring it from the certificate job that no longer has state.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0056_add_domain_live_check"
down_revision: str | None = "0055_backfill_photo_asset_kind"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "hospitals",
        sa.Column("domain_last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("hospitals", sa.Column("domain_last_check_ok", sa.Boolean(), nullable=True))
    op.add_column(
        "hospitals",
        sa.Column("domain_last_check_reason", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hospitals", "domain_last_check_reason")
    op.drop_column("hospitals", "domain_last_check_ok")
    op.drop_column("hospitals", "domain_last_checked_at")
