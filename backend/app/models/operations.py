import uuid
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


def _jsonb_type() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


class IncidentState(StrEnum):
    OPEN = "OPEN"
    RETRYING = "RETRYING"
    RECOVERED = "RECOVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class IncidentSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class OperationRunState(StrEnum):
    REQUESTED = "REQUESTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NotificationOutboxState(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    RETRYING = "RETRYING"
    HOLD = "HOLD"
    SENT = "SENT"
    FAILED = "FAILED"


class OperationRun(Base):
    """Durable, idempotent record of one operator or scheduler command."""

    __tablename__ = "operation_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('REQUESTED', 'QUEUED', 'RUNNING', 'SUCCEEDED', "
            "'PARTIAL', 'FAILED', 'CANCELLED')",
            name="ck_operation_runs_state",
        ),
        CheckConstraint("version >= 1", name="ck_operation_runs_version_positive"),
        CheckConstraint("attempt_count >= 0", name="ck_operation_runs_attempt_count"),
        CheckConstraint(
            "total_count >= 0 AND success_count >= 0 AND failure_count >= 0 "
            "AND skipped_count >= 0 AND success_count + failure_count + skipped_count "
            "<= total_count",
            name="ck_operation_runs_counts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_operation_runs_lease_pair",
        ),
        Index(
            "uq_operation_runs_active_idempotency",
            "requested_by_id",
            "hospital_id",
            "operation_type",
            "idempotency_key",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text(
                "idempotency_key IS NOT NULL AND state IN "
                "('REQUESTED', 'QUEUED', 'RUNNING')"
            ),
        ),
        Index(
            "uq_operation_runs_idempotency_scope",
            "requested_by_id",
            "hospital_id",
            "operation_type",
            "idempotency_key",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "ix_operation_runs_claim",
            "state",
            "lease_expires_at",
            "created_at",
            postgresql_where=text("state IN ('REQUESTED', 'QUEUED', 'RUNNING')"),
        ),
        Index("ix_operation_runs_hospital_created", "hospital_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL")
    )
    operation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), default=OperationRunState.REQUESTED, server_default="REQUESTED", nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("operation_runs.id", ondelete="SET NULL")
    )
    task_id: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    request_payload: Mapped[dict[str, JSONValue]] = mapped_column(
        _jsonb_type(), default=dict, nullable=False
    )
    result_summary: Mapped[dict[str, JSONValue] | None] = mapped_column(_jsonb_type())
    safe_error_code: Mapped[str | None] = mapped_column(String(100))
    safe_error_message: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Incident(Base):
    """Exceptional operational state projected to Admin and Slack."""

    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_incidents_dedupe_key"),
        CheckConstraint(
            "state IN ('OPEN', 'RETRYING', 'RECOVERED', 'ACKNOWLEDGED')",
            name="ck_incidents_state",
        ),
        CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_incidents_severity",
        ),
        CheckConstraint("version >= 1", name="ck_incidents_version_positive"),
        CheckConstraint("occurrence_count >= 1", name="ck_incidents_occurrence_count"),
        CheckConstraint(
            "(state IN ('RECOVERED', 'ACKNOWLEDGED') AND recovered_at IS NOT NULL) OR "
            "(state IN ('OPEN', 'RETRYING') AND recovered_at IS NULL)",
            name="ck_incidents_recovery_fact",
        ),
        CheckConstraint(
            "(state = 'ACKNOWLEDGED' AND acknowledged_at IS NOT NULL "
            "AND acknowledged_by_id IS NOT NULL) OR "
            "(state <> 'ACKNOWLEDGED' AND acknowledged_at IS NULL "
            "AND acknowledged_by_id IS NULL)",
            name="ck_incidents_acknowledgement_fact",
        ),
        Index("ix_incidents_state_sla", "state", "sla_due_at", "last_seen_at"),
        Index("ix_incidents_hospital_state", "hospital_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL")
    )
    operation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("operation_runs.id", ondelete="SET NULL")
    )
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    incident_type: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), default=IncidentState.OPEN, server_default="OPEN", nullable=False
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_impact: Mapped[str] = mapped_column(String(500), nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(255))
    safe_error_code: Mapped[str | None] = mapped_column(String(100))
    safe_error_message: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str] = mapped_column(String(500), nullable=False)
    admin_path: Mapped[str] = mapped_column(String(500), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    occurrence_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NotificationOutbox(Base):
    """Durable notification intent; transport side effects happen after commit."""

    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_notification_outbox_dedupe_key"),
        CheckConstraint(
            "state IN ('PENDING', 'SENDING', 'RETRYING', 'HOLD', 'SENT', 'FAILED')",
            name="ck_notification_outbox_state",
        ),
        CheckConstraint("version >= 1", name="ck_notification_outbox_version_positive"),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_notification_outbox_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_notification_outbox_lease_pair",
        ),
        CheckConstraint(
            "(state = 'SENT' AND sent_at IS NOT NULL) OR "
            "(state <> 'SENT' AND sent_at IS NULL)",
            name="ck_notification_outbox_sent_fact",
        ),
        Index(
            "ix_notification_outbox_claim",
            "state",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("state IN ('PENDING', 'RETRYING')"),
        ),
        Index(
            "ix_notification_outbox_lease",
            "state",
            "lease_expires_at",
            postgresql_where=text("state = 'SENDING'"),
        ),
        Index("ix_notification_outbox_hospital_created", "hospital_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL")
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL")
    )
    operation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("operation_runs.id", ondelete="SET NULL")
    )
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), default=NotificationOutboxState.PENDING, server_default="PENDING", nullable=False
    )
    payload: Mapped[dict[str, JSONValue]] = mapped_column(_jsonb_type(), nullable=False)
    fallback_text: Mapped[str] = mapped_column(String(1000), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    provider_response: Mapped[dict[str, JSONValue] | None] = mapped_column(_jsonb_type())
    safe_error_code: Mapped[str | None] = mapped_column(String(100))
    safe_error_message: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
