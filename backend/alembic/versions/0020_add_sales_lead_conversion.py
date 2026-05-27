"""add sales lead conversion tracking

Revision ID: 0020_add_sales_lead_conversion
Revises: 0019_add_hospital_entity_identifiers
Create Date: 2026-05-15
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_add_sales_lead_conversion"
down_revision = "0019_add_hospital_entity_identifiers"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_table("sales_leads"):
        return

    if not _has_column("sales_leads", "status"):
        op.add_column(
            "sales_leads",
            sa.Column("status", sa.String(length=40), nullable=False, server_default="NEW"),
        )
    if not _has_column("sales_leads", "converted_hospital_id"):
        op.add_column(
            "sales_leads",
            sa.Column("converted_hospital_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    if not _has_column("sales_leads", "converted_at"):
        op.add_column(
            "sales_leads",
            sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column("sales_leads", "conversion_note"):
        op.add_column("sales_leads", sa.Column("conversion_note", sa.Text(), nullable=True))
    if not _has_column("sales_leads", "notification_status"):
        op.add_column(
            "sales_leads",
            sa.Column("notification_status", sa.String(length=40), nullable=True),
        )
    if not _has_column("sales_leads", "notification_error"):
        op.add_column("sales_leads", sa.Column("notification_error", sa.Text(), nullable=True))


def downgrade() -> None:
    if not _has_table("sales_leads"):
        return

    for column in (
        "notification_error",
        "notification_status",
        "conversion_note",
        "converted_at",
        "converted_hospital_id",
        "status",
    ):
        if _has_column("sales_leads", column):
            op.drop_column("sales_leads", column)
