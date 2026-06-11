"""add index on hospitals.aeo_domain + normalize stored values

Revision ID: 0025_add_aeo_domain_index
Revises: 0024_audit_log_append_only
Create Date: 2026-06-11

커스텀 도메인 서빙:
- 공개 /site 미들웨어가 요청 호스트(hospital.aeo_domain)로 병원을 역조회한다.
  요청당 1회(캐시 전) 발생하는 lookup이므로 인덱스를 추가한다.
- lookup은 정규화된 소문자 호스트명 동등 비교 — 기존 행도 같은 규칙으로
  정규화(소문자 + 끝 점 제거)해 일반 인덱스가 그대로 사용되게 한다.
  유일성은 앱 레벨(connect_domain의 409)에서 강제한다 — 레거시 중복 행이
  있어도 마이그레이션이 실패하지 않도록 DB unique 제약은 걸지 않는다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0025_add_aeo_domain_index"
down_revision = "0024_audit_log_append_only"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_hospitals_aeo_domain"


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(ix["name"] == index_name for ix in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("hospitals"):
        return

    # 기존 값 정규화 — lookup(소문자 동등 비교)과 저장 규칙을 일치시킨다.
    op.execute(
        sa.text(
            "UPDATE hospitals "
            "SET aeo_domain = lower(trim(trailing '.' from trim(aeo_domain))) "
            "WHERE aeo_domain IS NOT NULL"
        )
    )

    if not _has_index("hospitals", INDEX_NAME):
        op.create_index(INDEX_NAME, "hospitals", ["aeo_domain"])


def downgrade() -> None:
    if not _has_table("hospitals"):
        return
    if _has_index("hospitals", INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name="hospitals")
