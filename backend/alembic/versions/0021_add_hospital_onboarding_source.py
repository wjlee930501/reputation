"""add hospital onboarding source fields

Revision ID: 0021_add_hospital_onboarding_source
Revises: 0020_add_sales_lead_conversion
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_add_hospital_onboarding_source"
down_revision = "0020_add_sales_lead_conversion"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_table("hospitals"):
        return

    if not _has_column("hospitals", "source_lead_id"):
        op.add_column(
            "hospitals",
            sa.Column("source_lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        if _has_table("sales_leads"):
            op.create_foreign_key(
                "fk_hospitals_source_lead_id_sales_leads",
                "hospitals",
                "sales_leads",
                ["source_lead_id"],
                ["id"],
                ondelete="SET NULL",
            )
        op.create_index("ix_hospitals_source_lead_id", "hospitals", ["source_lead_id"])

    if not _has_column("hospitals", "onboarding_note"):
        op.add_column("hospitals", sa.Column("onboarding_note", sa.Text(), nullable=True))


def downgrade() -> None:
    if not _has_table("hospitals"):
        return

    if _has_column("hospitals", "onboarding_note"):
        op.drop_column("hospitals", "onboarding_note")

    if _has_column("hospitals", "source_lead_id"):
        op.drop_index("ix_hospitals_source_lead_id", table_name="hospitals")
        if _has_table("sales_leads"):
            op.drop_constraint(
                "fk_hospitals_source_lead_id_sales_leads",
                "hospitals",
                type_="foreignkey",
            )
        op.drop_column("hospitals", "source_lead_id")
