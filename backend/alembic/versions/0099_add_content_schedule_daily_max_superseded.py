"""Add content_schedules.daily_max and superseded_at.

daily_max: the most articles a publish day may hold (1~3). Existing rows default to 1, so
their slot placement is unchanged. superseded_at: set when a later human save with an earlier
start replaces a not-yet-started schedule of the same plan; such rows leave the effective-terms
ranking. Existing rows stay NULL. Additive with defaults, so no existing behaviour changes.

Revision ID: 0099_add_content_schedule_daily_max_superseded
Revises: 0098_add_doctor_survey_files
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0099_add_content_schedule_daily_max_superseded"
down_revision: str | None = "0098_add_doctor_survey_files"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "content_schedules",
        sa.Column("daily_max", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        "ck_content_schedules_daily_max_range",
        "content_schedules",
        "daily_max BETWEEN 1 AND 3",
    )
    op.add_column(
        "content_schedules",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("content_schedules", "superseded_at")
    op.drop_constraint(
        "ck_content_schedules_daily_max_range", "content_schedules", type_="check"
    )
    op.drop_column("content_schedules", "daily_max")
