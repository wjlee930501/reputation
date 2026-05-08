"""add admin audit logs

Revision ID: 0014_add_admin_audit_logs
Revises: 0013_add_sales_leads
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014_add_admin_audit_logs"
down_revision = "0013_add_sales_leads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=True),
        sa.Column("target_id", sa.String(length=80), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_audit_logs_hospital_created", "admin_audit_logs", ["hospital_id", "created_at"])
    op.create_index("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_logs_action", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_hospital_created", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
