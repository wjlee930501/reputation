"""add durable operations control plane

Revision ID: 0042_add_operations_control_plane
Revises: 0041_add_monthly_delivery_control
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0042_add_operations_control_plane"
down_revision: str | None = "0041_add_monthly_delivery_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def upgrade() -> None:
    op.create_table(
        "operation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True)),
        sa.Column("operation_type", sa.String(100), nullable=False),
        sa.Column("state", sa.String(20), server_default="REQUESTED", nullable=False),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True)),
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("task_id", sa.String(255)),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "request_payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("result_summary", postgresql.JSONB()),
        sa.Column("safe_error_code", sa.String(100)),
        sa.Column("safe_error_message", sa.Text()),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "state IN ('REQUESTED', 'QUEUED', 'RUNNING', 'SUCCEEDED', "
            "'PARTIAL', 'FAILED', 'CANCELLED')",
            name="ck_operation_runs_state",
        ),
        sa.CheckConstraint("version >= 1", name="ck_operation_runs_version_positive"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_operation_runs_attempt_count"),
        sa.CheckConstraint(
            "total_count >= 0 AND success_count >= 0 AND failure_count >= 0 "
            "AND skipped_count >= 0 AND success_count + failure_count + skipped_count "
            "<= total_count",
            name="ck_operation_runs_counts",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_operation_runs_lease_pair",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["requested_by_id"], ["admin_users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["operation_runs.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "uq_operation_runs_active_idempotency",
        "operation_runs",
        ["requested_by_id", "hospital_id", "operation_type", "idempotency_key"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text(
            "idempotency_key IS NOT NULL AND state IN ('REQUESTED', 'QUEUED', 'RUNNING')"
        ),
    )
    op.create_index(
        "uq_operation_runs_idempotency_scope",
        "operation_runs",
        ["requested_by_id", "hospital_id", "operation_type", "idempotency_key"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_operation_runs_claim",
        "operation_runs",
        ["state", "lease_expires_at", "created_at"],
        postgresql_where=sa.text("state IN ('REQUESTED', 'QUEUED', 'RUNNING')"),
    )
    op.create_index(
        "ix_operation_runs_hospital_created",
        "operation_runs",
        ["hospital_id", "created_at"],
    )

    op.create_table(
        "incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True)),
        sa.Column("operation_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("incident_type", sa.String(100), nullable=False),
        sa.Column("state", sa.String(20), server_default="OPEN", nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("customer_impact", sa.String(500), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True)),
        sa.Column("sla_due_at", sa.DateTime(timezone=True)),
        sa.Column("source_type", sa.String(100), nullable=False),
        sa.Column("source_id", sa.String(255)),
        sa.Column("safe_error_code", sa.String(100)),
        sa.Column("safe_error_message", sa.Text()),
        sa.Column("next_action", sa.String(500), nullable=False),
        sa.Column("admin_path", sa.String(500), nullable=False),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("occurrence_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by_id", postgresql.UUID(as_uuid=True)),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "state IN ('OPEN', 'RETRYING', 'RECOVERED', 'ACKNOWLEDGED')",
            name="ck_incidents_state",
        ),
        sa.CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_incidents_severity",
        ),
        sa.CheckConstraint("version >= 1", name="ck_incidents_version_positive"),
        sa.CheckConstraint(
            "(state IN ('RECOVERED', 'ACKNOWLEDGED') AND recovered_at IS NOT NULL) OR "
            "(state IN ('OPEN', 'RETRYING') AND recovered_at IS NULL)",
            name="ck_incidents_recovery_fact",
        ),
        sa.CheckConstraint("occurrence_count >= 1", name="ck_incidents_occurrence_count"),
        sa.CheckConstraint(
            "(state = 'ACKNOWLEDGED' AND acknowledged_at IS NOT NULL "
            "AND acknowledged_by_id IS NOT NULL) OR "
            "(state <> 'ACKNOWLEDGED' AND acknowledged_at IS NULL "
            "AND acknowledged_by_id IS NULL)",
            name="ck_incidents_acknowledgement_fact",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["operation_run_id"], ["operation_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_id"], ["admin_users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_incidents_dedupe_key"),
    )
    op.create_index(
        "ix_incidents_state_sla", "incidents", ["state", "sla_due_at", "last_seen_at"]
    )
    op.create_index(
        "ix_incidents_hospital_state", "incidents", ["hospital_id", "state"]
    )

    op.create_table(
        "notification_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True)),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True)),
        sa.Column("operation_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("notification_type", sa.String(100), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("state", sa.String(20), server_default="PENDING", nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fallback_text", sa.String(1000), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("lease_owner", sa.String(255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("provider_message_id", sa.String(255)),
        sa.Column("provider_response", postgresql.JSONB()),
        sa.Column("safe_error_code", sa.String(100)),
        sa.Column("safe_error_message", sa.Text()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "state IN ('PENDING', 'SENDING', 'RETRYING', 'HOLD', 'SENT', 'FAILED')",
            name="ck_notification_outbox_state",
        ),
        sa.CheckConstraint("version >= 1", name="ck_notification_outbox_version_positive"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_notification_outbox_attempts",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_notification_outbox_lease_pair",
        ),
        sa.CheckConstraint(
            "(state = 'SENT' AND sent_at IS NOT NULL) OR "
            "(state <> 'SENT' AND sent_at IS NULL)",
            name="ck_notification_outbox_sent_fact",
        ),
        sa.CheckConstraint(
            "(state IN ('PENDING', 'RETRYING') AND next_attempt_at IS NOT NULL) OR "
            "(state IN ('SENDING', 'HOLD', 'SENT', 'FAILED') "
            "AND next_attempt_at IS NULL)",
            name="ck_notification_outbox_retry_schedule",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["operation_run_id"], ["operation_runs.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_outbox_dedupe_key"),
    )
    op.create_index(
        "ix_notification_outbox_claim",
        "notification_outbox",
        ["state", "next_attempt_at", "created_at"],
        postgresql_where=sa.text("state IN ('PENDING', 'RETRYING')"),
    )
    op.create_index(
        "ix_notification_outbox_lease",
        "notification_outbox",
        ["state", "lease_expires_at"],
        postgresql_where=sa.text("state = 'SENDING'"),
    )
    op.create_index(
        "ix_notification_outbox_hospital_created",
        "notification_outbox",
        ["hospital_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_outbox_hospital_created", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_lease", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_claim", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_index("ix_incidents_hospital_state", table_name="incidents")
    op.drop_index("ix_incidents_state_sla", table_name="incidents")
    op.drop_table("incidents")
    op.drop_index("ix_operation_runs_hospital_created", table_name="operation_runs")
    op.drop_index("ix_operation_runs_claim", table_name="operation_runs")
    op.drop_index("uq_operation_runs_idempotency_scope", table_name="operation_runs")
    op.drop_index("uq_operation_runs_active_idempotency", table_name="operation_runs")
    op.drop_table("operation_runs")
