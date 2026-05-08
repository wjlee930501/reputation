"""add sales leads

Revision ID: 0013_add_sales_leads
Revises: 0012_add_exposure_content_link_uniqueness
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_add_sales_leads"
down_revision = "0012_add_exposure_content_link_uniqueness"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    if _has_table("sales_leads"):
        return
    op.create_table(
        "sales_leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("clinic_name", sa.String(200), nullable=False),
        sa.Column("clinic_type", sa.String(200), nullable=False),
        sa.Column("contact", sa.String(200), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("privacy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_path", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sales_leads_created_at", "sales_leads", ["created_at"])


def downgrade() -> None:
    if _has_table("sales_leads"):
        op.drop_index("ix_sales_leads_created_at", table_name="sales_leads")
        op.drop_table("sales_leads")
