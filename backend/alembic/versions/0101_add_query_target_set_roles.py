"""Add measurement set group and role to ai_query_targets (measurement 30).

`question_group` (A~D) and `set_role` (FIXED/ROTATING) mark a hospital's curated 30-question
monthly measurement set (docs/plans/2026-11-21-measurement-30-design.md §2). Purely additive:
NULL means "not part of a curated set", so hospitals keep the legacy 10~15 auto-selected set
and older code that ignores the columns still reads every row.

Revision ID: 0101_add_query_target_set_roles
Revises: 0100_add_hospital_site_configs
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0101_add_query_target_set_roles"
down_revision: str | None = "0100_add_hospital_site_configs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ai_query_targets", sa.Column("question_group", sa.String(length=1), nullable=True)
    )
    op.add_column("ai_query_targets", sa.Column("set_role", sa.String(length=10), nullable=True))
    op.create_check_constraint(
        "ck_ai_query_targets_question_group",
        "ai_query_targets",
        "question_group IS NULL OR question_group IN ('A', 'B', 'C', 'D')",
    )
    op.create_check_constraint(
        "ck_ai_query_targets_set_role",
        "ai_query_targets",
        "set_role IS NULL OR set_role IN ('FIXED', 'ROTATING')",
    )
    # A role without a group cannot be reported by group; keep the pair consistent.
    op.create_check_constraint(
        "ck_ai_query_targets_set_role_has_group",
        "ai_query_targets",
        "set_role IS NULL OR question_group IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ai_query_targets_set_role_has_group", "ai_query_targets", type_="check")
    op.drop_constraint("ck_ai_query_targets_set_role", "ai_query_targets", type_="check")
    op.drop_constraint("ck_ai_query_targets_question_group", "ai_query_targets", type_="check")
    op.drop_column("ai_query_targets", "set_role")
    op.drop_column("ai_query_targets", "question_group")
