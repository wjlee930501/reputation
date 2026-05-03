"""add ai query target strategy models

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if _has_table(table_name) and not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_table(table_name) and _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    if not _has_table("ai_query_targets"):
        op.create_table(
            "ai_query_targets",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "hospital_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("target_intent", sa.String(100), nullable=False),
            sa.Column("region_terms", _json_type(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("specialty", sa.String(200), nullable=True),
            sa.Column("condition_or_symptom", sa.String(200), nullable=True),
            sa.Column("treatment", sa.String(200), nullable=True),
            sa.Column("decision_criteria", _json_type(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("patient_language", sa.String(20), nullable=False, server_default="ko"),
            sa.Column("platforms", _json_type(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("competitor_names", _json_type(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("priority", sa.String(20), nullable=False, server_default="NORMAL"),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("target_month", sa.String(7), nullable=True),
            sa.Column("created_by", sa.String(100), nullable=True),
            sa.Column("updated_by", sa.String(100), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if not _has_table("ai_query_variants"):
        op.create_table(
            "ai_query_variants",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "query_target_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_query_targets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("query_text", sa.String(500), nullable=False),
            sa.Column("platform", sa.String(50), nullable=False, server_default="CHATGPT"),
            sa.Column("language", sa.String(20), nullable=False, server_default="ko"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "query_matrix_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("query_matrix.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    _create_index_if_missing("ix_ai_query_targets_hospital_id", "ai_query_targets", ["hospital_id"])
    _create_index_if_missing(
        "ix_ai_query_targets_hospital_status_priority_month",
        "ai_query_targets",
        ["hospital_id", "status", "priority", "target_month"],
    )
    _create_index_if_missing(
        "ix_ai_query_variants_query_target_id",
        "ai_query_variants",
        ["query_target_id"],
    )
    _create_index_if_missing(
        "ix_ai_query_variants_query_matrix_id",
        "ai_query_variants",
        ["query_matrix_id"],
    )
    _create_index_if_missing(
        "ix_ai_query_variants_target_active_platform",
        "ai_query_variants",
        ["query_target_id", "is_active", "platform"],
    )


def downgrade() -> None:
    _drop_index_if_exists("ix_ai_query_variants_target_active_platform", "ai_query_variants")
    _drop_index_if_exists("ix_ai_query_variants_query_matrix_id", "ai_query_variants")
    _drop_index_if_exists("ix_ai_query_variants_query_target_id", "ai_query_variants")
    _drop_index_if_exists("ix_ai_query_targets_hospital_status_priority_month", "ai_query_targets")
    _drop_index_if_exists("ix_ai_query_targets_hospital_id", "ai_query_targets")

    if _has_table("ai_query_variants"):
        op.drop_table("ai_query_variants")
    if _has_table("ai_query_targets"):
        op.drop_table("ai_query_targets")
