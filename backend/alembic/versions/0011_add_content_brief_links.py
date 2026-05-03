"""add content brief query links

Revision ID: 0011_add_content_brief_links
Revises: 0010_add_exposure_actions
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_add_content_brief_links"
down_revision = "0010_add_exposure_actions"
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


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if _has_table(table_name) and not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _has_table(table_name) and _has_column(table_name, column_name):
        op.drop_column(table_name, column_name)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if _has_table(table_name) and not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_table(table_name) and _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    _add_column_if_missing(
        "content_items",
        sa.Column(
            "query_target_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_query_targets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    _add_column_if_missing(
        "content_items",
        sa.Column(
            "exposure_action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exposure_actions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    _add_column_if_missing("content_items", sa.Column("content_brief", _json_type(), nullable=True))
    _add_column_if_missing("content_items", sa.Column("brief_status", sa.String(30), nullable=True))
    _add_column_if_missing(
        "content_items",
        sa.Column("brief_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "content_items",
        sa.Column("brief_approved_by", sa.String(100), nullable=True),
    )

    _create_index_if_missing("ix_content_items_query_target_id", "content_items", ["query_target_id"])
    _create_index_if_missing("ix_content_items_exposure_action_id", "content_items", ["exposure_action_id"])
    _create_index_if_missing(
        "ix_content_items_hospital_brief_status",
        "content_items",
        ["hospital_id", "brief_status"],
    )


def downgrade() -> None:
    _drop_index_if_exists("ix_content_items_hospital_brief_status", "content_items")
    _drop_index_if_exists("ix_content_items_exposure_action_id", "content_items")
    _drop_index_if_exists("ix_content_items_query_target_id", "content_items")

    _drop_column_if_exists("content_items", "brief_approved_by")
    _drop_column_if_exists("content_items", "brief_approved_at")
    _drop_column_if_exists("content_items", "brief_status")
    _drop_column_if_exists("content_items", "content_brief")
    _drop_column_if_exists("content_items", "exposure_action_id")
    _drop_column_if_exists("content_items", "query_target_id")
