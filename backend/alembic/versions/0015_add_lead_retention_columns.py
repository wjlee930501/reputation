"""add lead retention + consent trail columns

Revision ID: 0015_add_lead_retention_columns
Revises: 0014_add_admin_audit_logs
Create Date: 2026-05-08

개인정보보호법 제15조(동의) + 제21조(보관기간/파기) 컴플라이언스.
- consent_version: 처리방침 버전. 변경 시 재동의 필요.
- consent_ip: 동의 시점 IP. 분쟁/감사 trail.
- retain_until: 자동 파기 기한. 도달 시 purge_expired_leads cron이 정리.
- purged_at: 파기 완료 시각 (soft-delete 후 hard-delete 전환 가능).
"""
from datetime import timedelta

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_add_lead_retention_columns"
down_revision = "0014_add_admin_audit_logs"
branch_labels = None
depends_on = None


DEFAULT_RETENTION_DAYS = 180


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_table("sales_leads"):
        return

    is_postgres = op.get_bind().dialect.name == "postgresql"
    inet_type = postgresql.INET() if is_postgres else sa.String(length=64)

    if not _has_column("sales_leads", "consent_version"):
        op.add_column(
            "sales_leads",
            sa.Column("consent_version", sa.String(length=40), nullable=True),
        )
    if not _has_column("sales_leads", "consent_ip"):
        op.add_column(
            "sales_leads",
            sa.Column("consent_ip", inet_type, nullable=True),
        )
    if not _has_column("sales_leads", "retain_until"):
        op.add_column(
            "sales_leads",
            sa.Column("retain_until", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column("sales_leads", "purged_at"):
        op.add_column(
            "sales_leads",
            sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        )

    # 기존 lead 들에 보관기한 기본값 backfill (created_at + 180일)
    bind = op.get_bind()
    if is_postgres:
        bind.execute(
            sa.text(
                "UPDATE sales_leads "
                "SET retain_until = created_at + INTERVAL '180 days' "
                "WHERE retain_until IS NULL"
            )
        )
    else:
        # SQLite (테스트) — 180 day shift via datetime()
        bind.execute(
            sa.text(
                "UPDATE sales_leads "
                "SET retain_until = datetime(created_at, '+180 days') "
                "WHERE retain_until IS NULL"
            )
        )


def downgrade() -> None:
    if not _has_table("sales_leads"):
        return
    for column in ("purged_at", "retain_until", "consent_ip", "consent_version"):
        if _has_column("sales_leads", column):
            op.drop_column("sales_leads", column)
