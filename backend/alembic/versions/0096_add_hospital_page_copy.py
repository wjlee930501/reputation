"""Add per-hospital public page copy: care principles, parking and transit notes.

The home 'care principles' band and the visit tips were one template for every clinic.
Marketers now write them per hospital (2026-11-17 marketing request); NULL keeps the
shared default copy on the public site, so deploying this changes no live page.

Revision ID: 0096_add_hospital_page_copy
Revises: 0095_add_hospital_site_layout
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0096_add_hospital_page_copy"
down_revision: str | None = "0095_add_hospital_site_layout"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hospitals", sa.Column("care_principles", sa.JSON(), nullable=True))
    op.add_column("hospitals", sa.Column("parking_note", sa.String(length=200), nullable=True))
    op.add_column("hospitals", sa.Column("transit_note", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("hospitals", "transit_note")
    op.drop_column("hospitals", "parking_note")
    op.drop_column("hospitals", "care_principles")
