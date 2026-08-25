"""Repair monthly delivery schema drift left by an already-applied 0041.

Revision ID: 0058_repair_monthly_delivery_drift
Revises: 0057_mark_operations_test_accounts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

from alembic import op

revision: str = "0058_repair_monthly_delivery_drift"
down_revision: str | None = "0057_mark_operations_test_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_REPORT_UNIQUE = "uq_monthly_reports_hospital_period_type"
_VERSIONED_REPORT_UNIQUE = "uq_monthly_reports_period_version"


def _column_names(inspector: Inspector, table: str) -> set[str]:
    return {str(column["name"]) for column in inspector.get_columns(table)}


def _unique_names(inspector: Inspector, table: str) -> set[str]:
    return {
        str(constraint["name"])
        for constraint in inspector.get_unique_constraints(table)
        if constraint.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "monthly_measurement_manifests" in tables:
        manifest_columns = _column_names(inspector, "monthly_measurement_manifests")
        if "closes_at" not in manifest_columns:
            op.add_column(
                "monthly_measurement_manifests",
                sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
            )
            # Monthly periods close at 00:15 KST on the first day of the next month.
            # Reconstruct the deterministic cutoff instead of guessing from migration time.
            op.execute(
                sa.text(
                    """
                    UPDATE monthly_measurement_manifests
                    SET closes_at = (
                        make_date(period_year, period_month, 1)
                        + interval '1 month 15 minutes'
                    ) AT TIME ZONE 'Asia/Seoul'
                    WHERE closes_at IS NULL
                    """
                )
            )
            op.alter_column(
                "monthly_measurement_manifests",
                "closes_at",
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
            )

    if "monthly_reports" in tables:
        report_uniques = _unique_names(inspector, "monthly_reports")
        if _LEGACY_REPORT_UNIQUE in report_uniques:
            op.drop_constraint(_LEGACY_REPORT_UNIQUE, "monthly_reports", type_="unique")
        if _VERSIONED_REPORT_UNIQUE not in report_uniques:
            op.create_unique_constraint(
                _VERSIONED_REPORT_UNIQUE,
                "monthly_reports",
                ["hospital_id", "period_year", "period_month", "report_type", "version"],
            )


def downgrade() -> None:
    # This revision repairs live drift. Re-introducing the legacy one-report-per-period
    # constraint or removing closes_at would make existing version history invalid.
    pass
