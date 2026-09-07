import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.sov import SovRecord


class ReportArtifactState(StrEnum):
    MISSING = "MISSING"
    INVALID = "INVALID"
    VALID = "VALID"


class ReportDeliveryEventType(StrEnum):
    DELIVERED = "DELIVERED"
    CORRECTED = "CORRECTED"
    RESCINDED = "RESCINDED"
    REDELIVERED = "REDELIVERED"


class MonthlyMeasurementManifest(Base):
    """Frozen monthly denominator and its platform configuration provenance."""

    __tablename__ = "monthly_measurement_manifests"
    __table_args__ = (
        UniqueConstraint(
            "hospital_id", "period_year", "period_month", name="uq_monthly_manifest_period"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False
    )
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    configured_platforms: Mapped[list] = mapped_column(JSON, nullable=False)
    platform_provenance: Mapped[dict] = mapped_column(JSON, nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cells: Mapped[list["MonthlyMeasurementCell"]] = relationship(
        back_populates="manifest",
        cascade="all, delete-orphan",
        order_by="MonthlyMeasurementCell.query_key",
    )


class MonthlyMeasurementCell(Base):
    """One immutable query target or variant by configured platform."""

    __tablename__ = "monthly_measurement_cells"
    __table_args__ = (
        UniqueConstraint(
            "manifest_id", "query_key", "platform", name="uq_monthly_cell_key_platform"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monthly_measurement_manifests.id", ondelete="CASCADE"), nullable=False
    )
    query_key: Mapped[str] = mapped_column(String(100), nullable=False)
    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    query_matrix_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_matrix.id", ondelete="RESTRICT")
    )
    query_target_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_query_targets.id", ondelete="RESTRICT")
    )
    query_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_query_variants.id", ondelete="RESTRICT")
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), default="FAILED", server_default="FAILED", nullable=False
    )
    exclusion_reason: Mapped[str | None] = mapped_column(String(50))
    excluded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    manifest: Mapped[MonthlyMeasurementManifest] = relationship(back_populates="cells")
    attempts: Mapped[list["MonthlyMeasurementAttempt"]] = relationship(
        back_populates="cell", cascade="all, delete-orphan"
    )
    observation_slots: Mapped[list["MeasurementObservationSlot"]] = relationship(
        back_populates="monthly_cell", cascade="all, delete-orphan"
    )


class MonthlyMeasurementAttempt(Base):
    """Append-only link retaining every attempt while selecting earliest success."""

    __tablename__ = "monthly_measurement_attempts"
    __table_args__ = (UniqueConstraint("sov_record_id", name="uq_monthly_attempt_sov_record"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cell_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monthly_measurement_cells.id", ondelete="CASCADE"), nullable=False
    )
    sov_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sov_records.id", ondelete="RESTRICT"), nullable=False
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    cell: Mapped[MonthlyMeasurementCell] = relationship(back_populates="attempts")
    sov_record: Mapped["SovRecord"] = relationship()


class MeasurementObservationSlot(Base):
    """Durable answer/judgment checkpoint for one paid observation repeat.

    New monthly and V0 work gets an explicit, immutable repeat identity. Existing
    SovRecord rows are deliberately not backfilled: their repeat/protocol lineage
    cannot be reconstructed truthfully and remains legacy evidence.
    """

    __tablename__ = "measurement_observation_slots"
    __table_args__ = (
        CheckConstraint("scope IN ('MONTHLY', 'V0')", name="ck_measurement_slot_scope"),
        CheckConstraint("repeat_no > 0", name="ck_measurement_slot_repeat_no"),
        CheckConstraint(
            "(scope = 'MONTHLY' AND monthly_cell_id IS NOT NULL) OR "
            "(scope = 'V0' AND monthly_cell_id IS NULL)",
            name="ck_measurement_slot_scope_shape",
        ),
        CheckConstraint(
            "answer_status IN ('PENDING', 'RECEIVED', 'FAILED')",
            name="ck_measurement_slot_answer_status",
        ),
        CheckConstraint(
            "judgment_status IN ('PENDING', 'CONFIRMED', 'AMBIGUOUS', 'FAILED')",
            name="ck_measurement_slot_judgment_status",
        ),
        CheckConstraint(
            "answer_attempt_count >= 0 AND judgment_attempt_count >= 0 AND version > 0",
            name="ck_measurement_slot_counters",
        ),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name="ck_measurement_slot_lease_pair",
        ),
        Index(
            "uq_measurement_slot_monthly_repeat",
            "monthly_cell_id",
            "repeat_no",
            unique=True,
            postgresql_where=text("scope = 'MONTHLY'"),
        ),
        Index(
            "uq_measurement_slot_v0_repeat",
            "measurement_run_id",
            "query_id",
            "platform",
            "repeat_no",
            unique=True,
            postgresql_where=text("scope = 'V0'"),
        ),
        Index("ix_measurement_slots_run_status", "measurement_run_id", "judgment_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False
    )
    monthly_cell_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("monthly_measurement_cells.id", ondelete="CASCADE")
    )
    measurement_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("measurement_runs.id", ondelete="CASCADE"), nullable=False
    )
    query_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("query_matrix.id", ondelete="RESTRICT"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    repeat_no: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    answer_status: Mapped[str] = mapped_column(
        String(20), default="PENDING", server_default="PENDING", nullable=False
    )
    raw_response: Mapped[str | None] = mapped_column(Text)
    answer_hash: Mapped[str | None] = mapped_column(String(64))
    answer_model: Mapped[str | None] = mapped_column(String(100))
    measurement_method: Mapped[str | None] = mapped_column(String(100))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_calls: Mapped[int | None] = mapped_column(Integer)
    citation_urls: Mapped[list | None] = mapped_column(JSON)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    answer_failure_reason: Mapped[str | None] = mapped_column(String(500))
    answer_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    judgment_status: Mapped[str] = mapped_column(
        String(20), default="PENDING", server_default="PENDING", nullable=False
    )
    judgment_input_fingerprint: Mapped[str | None] = mapped_column(String(64))
    judgment_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    judgment_failure_reason: Mapped[str | None] = mapped_column(String(500))
    sov_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "sov_records.id",
            deferrable=True,
            initially="DEFERRED",
        ),
        unique=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    monthly_cell: Mapped["MonthlyMeasurementCell | None"] = relationship(
        back_populates="observation_slots"
    )
    sov_record: Mapped["SovRecord | None"] = relationship()


class HospitalServiceInterval(Base):
    __tablename__ = "hospital_service_intervals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MonthlyReportArtifact(Base):
    __tablename__ = "monthly_report_artifacts"
    __table_args__ = (
        CheckConstraint(
            "(validated = false AND validated_at IS NULL AND validated_by_id IS NULL) OR "
            "(validated = true AND validated_at IS NOT NULL "
            "AND validation_metadata IS NOT NULL AND (validated_by_id IS NOT NULL OR "
            "validation_metadata->>'validation_source' = 'SYSTEM'))",
            name="ck_monthly_artifact_validation",
        ),
        UniqueConstraint("report_id", "audience", name="uq_monthly_report_artifact_audience"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monthly_reports.id", ondelete="CASCADE"), nullable=False
    )
    audience: Mapped[str] = mapped_column(String(20), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    validated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))
    validation_metadata: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MonthlyDeliveryEvent(Base):
    """Append-only audit record for report delivery state changes."""

    __tablename__ = "monthly_delivery_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monthly_reports.id", ondelete="RESTRICT"), nullable=False
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("monthly_report_artifacts.id", ondelete="RESTRICT")
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))
    recipient: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
