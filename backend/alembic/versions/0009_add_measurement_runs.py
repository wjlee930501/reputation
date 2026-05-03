"""add measurement run foundation

Revision ID: 0009_add_measurement_runs
Revises: 0008_dedupe_ai_query_variants
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_add_measurement_runs"
down_revision = "0008_dedupe_ai_query_variants"
branch_labels = None
depends_on = None

FK_SOV_MEASUREMENT_RUN = "fk_sov_records_measurement_run_id_measurement_runs"
FK_SOV_AI_QUERY_TARGET = "fk_sov_records_ai_query_target_id_ai_query_targets"
FK_SOV_AI_QUERY_VARIANT = "fk_sov_records_ai_query_variant_id_ai_query_variants"


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


def _has_fk(table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(fk["name"] == constraint_name for fk in inspector.get_foreign_keys(table_name))


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if _has_table(table_name) and not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_table(table_name) and _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _create_fk_if_missing(
    constraint_name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
    ondelete: str,
) -> None:
    if (
        _has_table(source_table)
        and _has_table(referent_table)
        and not _has_fk(source_table, constraint_name)
    ):
        op.create_foreign_key(
            constraint_name,
            source_table,
            referent_table,
            local_cols,
            remote_cols,
            ondelete=ondelete,
        )


def _drop_fk_if_exists(table_name: str, constraint_name: str) -> None:
    if _has_table(table_name) and _has_fk(table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")


def _add_sov_column_if_missing(column: sa.Column) -> None:
    if _has_table("sov_records") and not _has_column("sov_records", column.name):
        op.add_column("sov_records", column)


def _drop_sov_column_if_exists(column_name: str) -> None:
    if _has_table("sov_records") and _has_column("sov_records", column_name):
        op.drop_column("sov_records", column_name)


def upgrade() -> None:
    if not _has_table("measurement_runs"):
        op.create_table(
            "measurement_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "hospital_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("run_label", sa.String(200), nullable=True),
            sa.Column(
                "measurement_method",
                sa.String(50),
                nullable=False,
                server_default="OPENAI_RESPONSE",
            ),
            sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
            sa.Column("query_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("model_name", sa.String(100), nullable=True),
            sa.Column("search_mode", sa.String(50), nullable=True),
            sa.Column("config", _json_type(), nullable=True),
            sa.Column("error_summary", _json_type(), nullable=True),
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

    _create_index_if_missing(
        "ix_measurement_runs_hospital_created",
        "measurement_runs",
        ["hospital_id", "created_at"],
    )
    _create_index_if_missing(
        "ix_measurement_runs_hospital_status",
        "measurement_runs",
        ["hospital_id", "status"],
    )

    _add_sov_column_if_missing(
        sa.Column("measurement_run_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    _add_sov_column_if_missing(
        sa.Column("ai_query_target_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    _add_sov_column_if_missing(
        sa.Column("ai_query_variant_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    _add_sov_column_if_missing(
        sa.Column(
            "measurement_method",
            sa.String(50),
            nullable=True,
            server_default="OPENAI_RESPONSE",
        )
    )
    _add_sov_column_if_missing(
        sa.Column("measurement_status", sa.String(50), nullable=True, server_default="SUCCESS")
    )
    _add_sov_column_if_missing(sa.Column("failure_reason", sa.Text(), nullable=True))
    _add_sov_column_if_missing(sa.Column("source_urls", _json_type(), nullable=True))

    _create_fk_if_missing(
        FK_SOV_MEASUREMENT_RUN,
        "sov_records",
        "measurement_runs",
        ["measurement_run_id"],
        ["id"],
        "SET NULL",
    )
    _create_fk_if_missing(
        FK_SOV_AI_QUERY_TARGET,
        "sov_records",
        "ai_query_targets",
        ["ai_query_target_id"],
        ["id"],
        "SET NULL",
    )
    _create_fk_if_missing(
        FK_SOV_AI_QUERY_VARIANT,
        "sov_records",
        "ai_query_variants",
        ["ai_query_variant_id"],
        ["id"],
        "SET NULL",
    )

    _create_index_if_missing("ix_sov_records_measurement_run_id", "sov_records", ["measurement_run_id"])
    _create_index_if_missing("ix_sov_records_ai_query_target_id", "sov_records", ["ai_query_target_id"])
    _create_index_if_missing("ix_sov_records_ai_query_variant_id", "sov_records", ["ai_query_variant_id"])

    if _has_table("sov_records"):
        op.execute(
            """
            UPDATE sov_records
            SET measurement_method = 'OPENAI_RESPONSE'
            WHERE measurement_method IS NULL
            """
        )
        op.execute(
            """
            UPDATE sov_records
            SET measurement_status = 'SUCCESS'
            WHERE measurement_status IS NULL
            """
        )


def downgrade() -> None:
    _drop_index_if_exists("ix_sov_records_ai_query_variant_id", "sov_records")
    _drop_index_if_exists("ix_sov_records_ai_query_target_id", "sov_records")
    _drop_index_if_exists("ix_sov_records_measurement_run_id", "sov_records")

    _drop_fk_if_exists("sov_records", FK_SOV_AI_QUERY_VARIANT)
    _drop_fk_if_exists("sov_records", FK_SOV_AI_QUERY_TARGET)
    _drop_fk_if_exists("sov_records", FK_SOV_MEASUREMENT_RUN)

    _drop_sov_column_if_exists("source_urls")
    _drop_sov_column_if_exists("failure_reason")
    _drop_sov_column_if_exists("measurement_status")
    _drop_sov_column_if_exists("measurement_method")
    _drop_sov_column_if_exists("ai_query_variant_id")
    _drop_sov_column_if_exists("ai_query_target_id")
    _drop_sov_column_if_exists("measurement_run_id")

    _drop_index_if_exists("ix_measurement_runs_hospital_status", "measurement_runs")
    _drop_index_if_exists("ix_measurement_runs_hospital_created", "measurement_runs")
    if _has_table("measurement_runs"):
        op.drop_table("measurement_runs")
