"""add content_items.carried_over_from (월말 반려 이월 추적)

Revision ID: 0026_add_content_carried_over_from
Revises: 0025_add_aeo_domain_index
Create Date: 2026-06-11

월말 반려 carry-over:
- 반려 재스케줄(scheduled_date → 내일)이 원래 발행 예정일과 다른 달로 넘어가면
  원래 scheduled_date를 carried_over_from에 기록한다 (재반려 시 최초 값 유지).
- 야간 생성 배치가 이월분(carried_over_from IS NOT NULL)을 먼저 처리하고,
  아침 초안 Slack에 "(전월 이월 — 우선 검토)"를 붙이는 데 사용한다.
- 내부 운영 데이터 — 공개(/site) 응답에는 노출하지 않는다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0026_add_content_carried_over_from"
down_revision = "0025_add_aeo_domain_index"
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
    if not _has_column("content_items", "carried_over_from"):
        op.add_column(
            "content_items",
            sa.Column("carried_over_from", sa.Date(), nullable=True),
        )


def downgrade() -> None:
    if not _has_table("content_items"):
        return
    if _has_column("content_items", "carried_over_from"):
        op.drop_column("content_items", "carried_over_from")
