"""Keep QA admin accounts out of real-operation metrics.

Revision ID: 0057_mark_operations_test_accounts
Revises: 0056_add_domain_live_check
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0057_mark_operations_test_accounts"
down_revision: str | None = "0056_add_domain_live_check"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "admin_users",
        sa.Column(
            "is_operations_test",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Known durable QA identities remain available for audits and cleanup; only
    # their classification changes. No production row is deleted.
    op.execute(
        sa.text(
            """
            UPDATE admin_users
            SET is_operations_test = TRUE
            WHERE lower(email) LIKE 'operator.%.20260810@example.invalid'
               OR lower(name) LIKE 'codex e2e%'
            """
        )
    )


def downgrade() -> None:
    op.drop_column("admin_users", "is_operations_test")
