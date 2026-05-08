"""add content references list + body_updated_at

Revision ID: 0016_add_content_references_and_body_updated_at
Revises: 0015_add_lead_retention_columns
Create Date: 2026-05-08

GEO 신호 보강:
- references: JSONB list of {title, url} — 콘텐츠 본문 근거 자료. AI 인용 가능성 ↑
  AI 답변 안에 인용될 때 출처가 명시된 콘텐츠가 도메인 권위 신호로 작동.
- body_updated_at: timestamptz — 본문 마지막 수정 시각. dateModified를 published_at과
  분리해 freshness 신호를 정확하게 표현. 기존 row는 published_at 또는 generated_at 또는
  created_at 순서로 backfill.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_add_content_references_and_body_updated_at"
down_revision = "0015_add_lead_retention_columns"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_table("content_items"):
        return

    is_postgres = op.get_bind().dialect.name == "postgresql"
    json_type = postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.JSON()

    if not _has_column("content_items", "references_list"):
        op.add_column(
            "content_items",
            sa.Column("references_list", json_type, nullable=True),
        )
    if not _has_column("content_items", "body_updated_at"):
        op.add_column(
            "content_items",
            sa.Column("body_updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    # 기존 row backfill: published_at → generated_at → created_at 우선순위.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE content_items "
            "SET body_updated_at = COALESCE(published_at, generated_at, created_at) "
            "WHERE body_updated_at IS NULL"
        )
    )


def downgrade() -> None:
    if not _has_table("content_items"):
        return
    for column in ("body_updated_at", "references_list"):
        if _has_column("content_items", column):
            op.drop_column("content_items", column)
