"""add performance indexes and unique constraints

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    # SoV trend queries: filter by hospital_id + measured_at range
    op.create_index(
        "ix_sov_records_hospital_measured",
        "sov_records",
        ["hospital_id", "measured_at"],
    )
    # Query priority adjustment: filter by query_id + measured_at
    op.create_index(
        "ix_sov_records_query_measured",
        "sov_records",
        ["query_id", "measured_at"],
    )
    # Public content listing: hospital_id + status + published_at
    op.create_index(
        "ix_content_items_hospital_status_published",
        "content_items",
        ["hospital_id", "status", "published_at"],
    )
    # Prevent duplicate monthly reports (TOCTOU race condition)
    op.create_unique_constraint(
        "uq_monthly_reports_hospital_period_type",
        "monthly_reports",
        ["hospital_id", "period_year", "period_month", "report_type"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_monthly_reports_hospital_period_type", "monthly_reports")
    op.drop_index("ix_content_items_hospital_status_published", table_name="content_items")
    op.drop_index("ix_sov_records_query_measured", table_name="sov_records")
    op.drop_index("ix_sov_records_hospital_measured", table_name="sov_records")
