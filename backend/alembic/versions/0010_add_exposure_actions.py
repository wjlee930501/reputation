"""add exposure gap and action foundation

Revision ID: 0010_add_exposure_actions
Revises: 0009_add_measurement_runs
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_add_exposure_actions"
down_revision = "0009_add_measurement_runs"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if _has_table(table_name) and not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_table(table_name) and _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if _has_table(table_name) and not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    if not _has_table("exposure_gaps"):
        op.create_table(
            "exposure_gaps",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "hospital_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "query_target_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_query_targets.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("gap_type", sa.String(50), nullable=False),
            sa.Column("severity", sa.String(20), nullable=False, server_default="MEDIUM"),
            sa.Column(
                "evidence",
                _json_type(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "diagnosed_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"),
        )
    else:
        _add_column_if_missing(
            "exposure_gaps",
            sa.Column(
                "hospital_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
                nullable=False,
            ),
        )
        _add_column_if_missing(
            "exposure_gaps",
            sa.Column(
                "query_target_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_query_targets.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        _add_column_if_missing(
            "exposure_gaps",
            sa.Column("gap_type", sa.String(50), nullable=False),
        )
        _add_column_if_missing(
            "exposure_gaps",
            sa.Column("severity", sa.String(20), nullable=False, server_default="MEDIUM"),
        )
        _add_column_if_missing(
            "exposure_gaps",
            sa.Column(
                "evidence",
                _json_type(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
        _add_column_if_missing(
            "exposure_gaps",
            sa.Column(
                "diagnosed_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        _add_column_if_missing(
            "exposure_gaps",
            sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"),
        )

    if not _has_table("exposure_actions"):
        op.create_table(
            "exposure_actions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "hospital_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "query_target_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_query_targets.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "gap_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("exposure_gaps.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("action_type", sa.String(50), nullable=False),
            sa.Column("title", sa.String(300), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("owner", sa.String(100), nullable=True),
            sa.Column("due_month", sa.String(7), nullable=True),
            sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"),
            sa.Column(
                "linked_content_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("content_items.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "linked_report_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("monthly_reports.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    else:
        _add_column_if_missing(
            "exposure_actions",
            sa.Column(
                "hospital_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
                nullable=False,
            ),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column(
                "query_target_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_query_targets.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column(
                "gap_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("exposure_gaps.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column("action_type", sa.String(50), nullable=False),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column("title", sa.String(300), nullable=False),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column("description", sa.Text(), nullable=False),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column("owner", sa.String(100), nullable=True),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column("due_month", sa.String(7), nullable=True),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column(
                "linked_content_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("content_items.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column(
                "linked_report_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("monthly_reports.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        _add_column_if_missing(
            "exposure_actions",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    _create_index_if_missing(
        "ix_exposure_gaps_hospital_status",
        "exposure_gaps",
        ["hospital_id", "status"],
    )
    _create_index_if_missing(
        "ix_exposure_gaps_query_target_status",
        "exposure_gaps",
        ["query_target_id", "status"],
    )
    _create_index_if_missing(
        "ix_exposure_actions_hospital_status_due_month",
        "exposure_actions",
        ["hospital_id", "status", "due_month"],
    )
    _create_index_if_missing(
        "ix_exposure_actions_query_target_status",
        "exposure_actions",
        ["query_target_id", "status"],
    )
    _create_index_if_missing("ix_exposure_actions_gap_id", "exposure_actions", ["gap_id"])


def downgrade() -> None:
    _drop_index_if_exists("ix_exposure_actions_gap_id", "exposure_actions")
    _drop_index_if_exists("ix_exposure_actions_query_target_status", "exposure_actions")
    _drop_index_if_exists("ix_exposure_actions_hospital_status_due_month", "exposure_actions")
    _drop_index_if_exists("ix_exposure_gaps_query_target_status", "exposure_gaps")
    _drop_index_if_exists("ix_exposure_gaps_hospital_status", "exposure_gaps")

    if _has_table("exposure_actions"):
        op.drop_table("exposure_actions")
    if _has_table("exposure_gaps"):
        op.drop_table("exposure_gaps")
