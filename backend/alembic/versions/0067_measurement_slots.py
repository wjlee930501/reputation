"""Add durable paid measurement observation slots.

Revision ID: 0067_measurement_slots
Revises: 0066_content_contracts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0067_measurement_slots"
down_revision: str | None = "0066_content_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lead_diagnosis_results",
        sa.Column("judgment_input_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "measurement_observation_slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monthly_cell_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("measurement_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("query_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("repeat_no", sa.Integer(), nullable=False),
        sa.Column("protocol_hash", sa.String(length=64), nullable=False),
        sa.Column("answer_status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("answer_hash", sa.String(length=64), nullable=True),
        sa.Column("answer_model", sa.String(length=100), nullable=True),
        sa.Column("measurement_method", sa.String(length=100), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("search_calls", sa.Integer(), nullable=True),
        sa.Column("citation_urls", sa.JSON(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("answer_failure_reason", sa.String(length=500), nullable=True),
        sa.Column("answer_attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("judgment_status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("judgment_input_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("judgment_attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("judgment_failure_reason", sa.String(length=500), nullable=True),
        sa.Column("sov_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("scope IN ('MONTHLY', 'V0')", name="ck_measurement_slot_scope"),
        sa.CheckConstraint("repeat_no > 0", name="ck_measurement_slot_repeat_no"),
        sa.CheckConstraint(
            "(scope = 'MONTHLY' AND monthly_cell_id IS NOT NULL) OR "
            "(scope = 'V0' AND monthly_cell_id IS NULL)",
            name="ck_measurement_slot_scope_shape",
        ),
        sa.CheckConstraint(
            "answer_status IN ('PENDING', 'RECEIVED', 'FAILED')",
            name="ck_measurement_slot_answer_status",
        ),
        sa.CheckConstraint(
            "judgment_status IN ('PENDING', 'CONFIRMED', 'AMBIGUOUS', 'FAILED')",
            name="ck_measurement_slot_judgment_status",
        ),
        sa.CheckConstraint(
            "answer_attempt_count >= 0 AND judgment_attempt_count >= 0 AND version > 0",
            name="ck_measurement_slot_counters",
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name="ck_measurement_slot_lease_pair",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["monthly_cell_id"], ["monthly_measurement_cells.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["measurement_run_id"], ["measurement_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["query_id"], ["query_matrix.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["sov_record_id"],
            ["sov_records.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sov_record_id", name="uq_measurement_slot_sov_record"),
    )
    op.create_index(
        "uq_measurement_slot_monthly_repeat",
        "measurement_observation_slots",
        ["monthly_cell_id", "repeat_no"],
        unique=True,
        postgresql_where=sa.text("scope = 'MONTHLY'"),
    )
    op.create_index(
        "uq_measurement_slot_v0_repeat",
        "measurement_observation_slots",
        ["measurement_run_id", "query_id", "platform", "repeat_no"],
        unique=True,
        postgresql_where=sa.text("scope = 'V0'"),
    )
    op.create_index(
        "ix_measurement_slots_run_status",
        "measurement_observation_slots",
        ["measurement_run_id", "judgment_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_measurement_slots_run_status", table_name="measurement_observation_slots")
    op.drop_index("uq_measurement_slot_v0_repeat", table_name="measurement_observation_slots")
    op.drop_index("uq_measurement_slot_monthly_repeat", table_name="measurement_observation_slots")
    op.drop_table("measurement_observation_slots")
    op.drop_column("lead_diagnosis_results", "judgment_input_fingerprint")
