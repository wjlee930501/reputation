"""replace the legacy eight-post plan with the 12/16/20 tier contract

Revision ID: 0039_update_content_plan_tiers
Revises: 0038_add_doctor_report_path
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0039_update_content_plan_tiers"
down_revision: Union[str, None] = "0038_add_doctor_report_path"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE plan ADD VALUE IF NOT EXISTS 'PLAN_20'")
    op.execute(
        sa.text("UPDATE hospitals SET plan = 'PLAN_12' WHERE plan = 'PLAN_8'")
    )
    op.execute(
        sa.text(
            "UPDATE content_schedules SET plan = 'PLAN_12' WHERE plan = 'PLAN_8'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE hospitals SET plan = 'PLAN_16' WHERE plan = 'PLAN_20'")
    )
    op.execute(
        sa.text(
            "UPDATE content_schedules SET plan = 'PLAN_16' WHERE plan = 'PLAN_20'"
        )
    )
