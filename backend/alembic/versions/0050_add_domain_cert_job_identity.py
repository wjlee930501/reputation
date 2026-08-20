"""Add token-bound identity to custom-domain certificate jobs.

Revision ID: 0050_add_domain_cert_job_identity
Revises: 0049_add_domain_cert_job_tracking

Legacy ISSUING rows were request-bound and have no durable worker that can finish
them after this release. Mark them FAILED so an operator can safely retry through
the new token-bound worker path. Certificate Manager resource IDs are deterministic,
so retrying an already-started provider operation remains idempotent.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0050_add_domain_cert_job_identity"
down_revision: str | None = "0049_add_domain_cert_job_tracking"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "hospitals",
        sa.Column(
            "domain_cert_job_token",
            sa.String(length=36),
            nullable=True,
            comment="Unique lease token for the current certificate worker job",
        ),
    )
    op.add_column(
        "hospitals",
        sa.Column(
            "domain_cert_job_domain",
            sa.String(length=200),
            nullable=True,
            comment="Domain snapshot owned by the current certificate worker job",
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE hospitals
            SET domain_cert_job_state = 'FAILED',
                domain_cert_job_started_at = NULL,
                domain_cert_job_token = NULL,
                domain_cert_job_domain = aeo_domain
            WHERE domain_cert_job_state = 'ISSUING'
            """
        )
    )


def downgrade() -> None:
    op.drop_column("hospitals", "domain_cert_job_domain")
    op.drop_column("hospitals", "domain_cert_job_token")
