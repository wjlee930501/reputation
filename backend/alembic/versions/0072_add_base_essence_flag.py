"""Promote each hospital's latest approved philosophy to stable BaseEssence.

Revision ID: 0072_add_base_essence_flag
Revises: 0071_plan_enum_cleanup
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0072_add_base_essence_flag"
down_revision: str | None = "0071_plan_enum_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive + server default keeps old application instances able to insert rows
    # while a rolling deployment is in progress.
    op.add_column(
        "hospital_content_philosophies",
        sa.Column(
            "is_base",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # APPROVED is currently unique per hospital, but rank explicitly so the migration
    # is safe for databases imported from older releases that may predate that index.
    op.execute(
        """
        WITH latest_approved AS (
            SELECT DISTINCT ON (hospital_id) id
            FROM hospital_content_philosophies
            WHERE status = 'APPROVED'
            ORDER BY hospital_id, approved_at DESC NULLS LAST, version DESC, created_at DESC, id DESC
        )
        UPDATE hospital_content_philosophies AS philosophy
        SET is_base = true
        FROM latest_approved
        WHERE philosophy.id = latest_approved.id
        """
    )
    op.create_index(
        "uq_hospital_content_philosophies_one_base",
        "hospital_content_philosophies",
        ["hospital_id"],
        unique=True,
        postgresql_where=sa.text("is_base"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_hospital_content_philosophies_one_base",
        table_name="hospital_content_philosophies",
    )
    op.drop_column("hospital_content_philosophies", "is_base")
