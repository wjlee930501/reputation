"""Allow Admin-only inquiry diagnoses without free-diagnosis quota or locks.

Revision ID: 0076_inquiry_internal_diagnosis
Revises: 0075_add_hospital_fallback_image
"""

import sqlalchemy as sa

from alembic import op

revision = "0076_inquiry_internal_diagnosis"
down_revision = "0075_add_hospital_fallback_image"
branch_labels = None
depends_on = None

_CUSTOMER_DELIVERY = ("PENDING", "SENDING", "SENT", "FAILED")
_DELIVERY = (*_CUSTOMER_DELIVERY, "INTERNAL")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    # INQUIRY diagnostics are paid/internal work, not free-diagnosis applications. NULL means
    # that no customer lock and no daily slot were claimed.
    for column, existing_type in (
        ("applicant_email_hash", sa.String(length=64)),
        ("subject_phone_hash", sa.String(length=64)),
        ("slot_date", sa.Date()),
        ("slot_no", sa.Integer()),
    ):
        op.alter_column(
            "lead_diagnoses",
            column,
            existing_type=existing_type,
            nullable=True,
        )

    op.drop_constraint(
        "ck_lead_diagnoses_delivery_status", "lead_diagnoses", type_="check"
    )
    op.create_check_constraint(
        "ck_lead_diagnoses_delivery_status",
        "lead_diagnoses",
        _in_list("delivery_status", _DELIVERY),
    )

    # INTERNAL is valid before a report is ready: it is a permanent customer-delivery hold,
    # not evidence that a delivery was attempted.
    op.drop_constraint(
        "ck_lead_diagnoses_delivery_requires_report",
        "lead_diagnoses",
        type_="check",
    )
    op.create_check_constraint(
        "ck_lead_diagnoses_delivery_requires_report",
        "lead_diagnoses",
        "delivery_status IN ('PENDING', 'INTERNAL') "
        "OR report_status IN ('READY', 'PURGED')",
    )

    op.drop_constraint("ck_lead_deliveries_status", "lead_deliveries", type_="check")
    op.create_check_constraint(
        "ck_lead_deliveries_status",
        "lead_deliveries",
        _in_list("status", _DELIVERY),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE lead_diagnoses SET delivery_status = 'PENDING' "
        "WHERE delivery_status = 'INTERNAL'"
    )
    op.execute(
        "UPDATE lead_deliveries SET status = 'FAILED' WHERE status = 'INTERNAL'"
    )

    op.drop_constraint("ck_lead_deliveries_status", "lead_deliveries", type_="check")
    op.create_check_constraint(
        "ck_lead_deliveries_status",
        "lead_deliveries",
        _in_list("status", _CUSTOMER_DELIVERY),
    )
    op.drop_constraint(
        "ck_lead_diagnoses_delivery_requires_report",
        "lead_diagnoses",
        type_="check",
    )
    op.create_check_constraint(
        "ck_lead_diagnoses_delivery_requires_report",
        "lead_diagnoses",
        "delivery_status = 'PENDING' OR report_status IN ('READY', 'PURGED')",
    )
    op.drop_constraint(
        "ck_lead_diagnoses_delivery_status", "lead_diagnoses", type_="check"
    )
    op.create_check_constraint(
        "ck_lead_diagnoses_delivery_status",
        "lead_diagnoses",
        _in_list("delivery_status", _CUSTOMER_DELIVERY),
    )

    # Preserve downgradeability without deleting internal rows. Synthetic values do not map to
    # a real applicant or a free-diagnosis day; they only satisfy the historical NOT NULL schema.
    op.execute(
        "UPDATE lead_diagnoses "
        "SET applicant_email_hash = md5(id::text || '|internal-email') "
        "WHERE applicant_email_hash IS NULL"
    )
    op.execute(
        "UPDATE lead_diagnoses "
        "SET subject_phone_hash = md5(id::text || '|internal-phone') "
        "WHERE subject_phone_hash IS NULL"
    )
    op.execute(
        "WITH base AS ("
        " SELECT COALESCE(MAX(slot_no), 0) AS slot_no"
        " FROM lead_diagnoses WHERE slot_date = DATE '1970-01-01'"
        "), missing AS ("
        " SELECT pending.id, base.slot_no + "
        "row_number() OVER (ORDER BY pending.created_at, pending.id) AS slot_no"
        " FROM lead_diagnoses AS pending CROSS JOIN base"
        " WHERE pending.slot_date IS NULL OR pending.slot_no IS NULL"
        ") UPDATE lead_diagnoses AS diagnosis "
        "SET slot_date = DATE '1970-01-01', slot_no = missing.slot_no "
        "FROM missing WHERE diagnosis.id = missing.id"
    )
    for column, existing_type in (
        ("applicant_email_hash", sa.String(length=64)),
        ("subject_phone_hash", sa.String(length=64)),
        ("slot_date", sa.Date()),
        ("slot_no", sa.Integer()),
    ):
        op.alter_column(
            "lead_diagnoses",
            column,
            existing_type=existing_type,
            nullable=False,
        )
