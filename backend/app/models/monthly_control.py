import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.sov import SovRecord


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
