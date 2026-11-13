"""Add the marketer-selected public site layout (A/B/C) to hospitals.

NULL keeps the pre-layout renderer, so existing public sites do not change until a
marketer picks a layout for that hospital (2026-11-13 decision).

Revision ID: 0095_add_hospital_site_layout
Revises: 0093_add_hospital_search_verification
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0095_add_hospital_site_layout"
down_revision: str | None = "0093_add_hospital_search_verification"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hospitals", sa.Column("site_layout", sa.String(length=1), nullable=True))
    op.create_check_constraint(
        "ck_hospitals_site_layout",
        "hospitals",
        "site_layout IS NULL OR site_layout IN ('A', 'B', 'C')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_hospitals_site_layout", "hospitals", type_="check")
    op.drop_column("hospitals", "site_layout")
