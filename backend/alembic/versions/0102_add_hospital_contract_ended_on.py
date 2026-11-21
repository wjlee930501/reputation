"""Add hospitals.contract_ended_on: the contract end date, for admin grouping only.

A paused hospital whose contract end date has passed is shown as 「운영 종료」 in the admin
list, header and dashboard (2026-11 admin revamp, decision 1 option B). Nothing else reads it:
public visibility, publishing, service intervals and report targeting stay as for any paused
hospital. Purely additive — NULL keeps every hospital as before.

Revision ID: 0102_add_hospital_contract_ended_on
Revises: 0101_add_query_target_set_roles
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0102_add_hospital_contract_ended_on"
down_revision: str | None = "0101_add_query_target_set_roles"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hospitals", sa.Column("contract_ended_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("hospitals", "contract_ended_on")
