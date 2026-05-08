"""add exposure content link uniqueness

Revision ID: 0012_add_exposure_content_link_uniqueness
Revises: 0011_add_content_brief_links
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_add_exposure_content_link_uniqueness"
down_revision = "0011_add_content_brief_links"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _check_no_duplicates(table_name: str, column_name: str) -> None:
    """Preflight: refuse to add a unique index if duplicates already exist.

    PostgreSQL은 데이터에 중복이 있으면 partial unique index 생성 자체가 실패한다.
    여기서 미리 검사해 명확한 메시지를 던지면 운영자가 cleanup → re-run 절차를 빨리 잡을 수 있다.
    """
    if not _has_table(table_name):
        return
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            f"SELECT {column_name} AS dup_value, COUNT(*) AS dup_count "
            f"FROM {table_name} "
            f"WHERE {column_name} IS NOT NULL "
            f"GROUP BY {column_name} "
            f"HAVING COUNT(*) > 1 "
            f"LIMIT 5"
        )
    )
    duplicates = result.fetchall()
    if duplicates:
        sample = ", ".join(str(row.dup_value) for row in duplicates)
        raise RuntimeError(
            f"Migration 0012 aborted: duplicate values exist in {table_name}.{column_name} "
            f"(sample: {sample}). Resolve duplicates manually before re-running. "
            f"Suggested cleanup: keep the most recent row per {column_name} and NULL the rest."
        )


def _create_unique_index_if_missing(
    index_name: str,
    table_name: str,
    column_name: str,
) -> None:
    if not _has_table(table_name) or _has_index(table_name, index_name):
        return
    _check_no_duplicates(table_name, column_name)
    op.create_index(
        index_name,
        table_name,
        [column_name],
        unique=True,
        postgresql_where=sa.text(f"{column_name} IS NOT NULL"),
    )


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_table(table_name) and _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    _create_unique_index_if_missing(
        "uq_content_items_exposure_action_id_not_null",
        "content_items",
        "exposure_action_id",
    )
    _create_unique_index_if_missing(
        "uq_exposure_actions_linked_content_id_not_null",
        "exposure_actions",
        "linked_content_id",
    )


def downgrade() -> None:
    _drop_index_if_exists("uq_exposure_actions_linked_content_id_not_null", "exposure_actions")
    _drop_index_if_exists("uq_content_items_exposure_action_id_not_null", "content_items")
