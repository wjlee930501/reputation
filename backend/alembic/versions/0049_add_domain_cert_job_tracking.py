"""Add domain certificate job tracking fields to Hospital.

Revision ID: 0049_add_domain_cert_job_tracking
Revises: 0048_normalize_partial_v0_summaries

Tracks certificate provisioning job state for custom domains (DM-F1, DM-F2).
Separates DNS verification success (operator-complete) from certificate issuance
(system follow-up) so onboarding step 5 can complete without blocking on Let's Encrypt.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0049_add_domain_cert_job_tracking"
down_revision: str | None = "0048_normalize_partial_v0_summaries"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "hospitals",
        sa.Column(
            "domain_cert_job_state",
            sa.String(length=20),
            nullable=True,
            comment="Certificate provisioning job state: WAITING|ISSUING|DONE|FAILED",
        ),
    )
    op.add_column(
        "hospitals",
        sa.Column(
            "domain_cert_job_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Certificate provisioning job start timestamp",
        ),
    )
    op.add_column(
        "hospitals",
        sa.Column(
            "domain_cert_dns_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="DNS verification success timestamp (operator-complete for step 5)",
        ),
    )


def downgrade() -> None:
    op.drop_column("hospitals", "domain_cert_dns_verified_at")
    op.drop_column("hospitals", "domain_cert_job_started_at")
    op.drop_column("hospitals", "domain_cert_job_state")
