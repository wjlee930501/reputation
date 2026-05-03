import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.content import ContentItem
    from app.models.hospital import Hospital
    from app.models.report import MonthlyReport


class QueryMatrix(Base):
    __tablename__ = "query_matrix"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))
    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[str] = mapped_column(String(20), default="NORMAL")  # HIGH, NORMAL, LOW
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hospital: Mapped["Hospital"] = relationship(back_populates="query_matrix")
    sov_records: Mapped[list["SovRecord"]] = relationship(
        back_populates="query", cascade="all, delete-orphan"
    )
    query_variants: Mapped[list["AIQueryVariant"]] = relationship(back_populates="query_matrix")


class AIQueryTarget(Base):
    __tablename__ = "ai_query_targets"
    __table_args__ = (
        Index("ix_ai_query_targets_hospital_id", "hospital_id"),
        Index(
            "ix_ai_query_targets_hospital_status_priority_month",
            "hospital_id",
            "status",
            "priority",
            "target_month",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_intent: Mapped[str] = mapped_column(String(100), nullable=False)
    region_terms: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    specialty: Mapped[str | None] = mapped_column(String(200))
    condition_or_symptom: Mapped[str | None] = mapped_column(String(200))
    treatment: Mapped[str | None] = mapped_column(String(200))
    decision_criteria: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    patient_language: Mapped[str] = mapped_column(String(20), default="ko", nullable=False)
    platforms: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    competitor_names: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="NORMAL", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    target_month: Mapped[str | None] = mapped_column(String(7))
    created_by: Mapped[str | None] = mapped_column(String(100))
    updated_by: Mapped[str | None] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    hospital: Mapped["Hospital"] = relationship(back_populates="query_targets")
    variants: Mapped[list["AIQueryVariant"]] = relationship(
        back_populates="query_target",
        cascade="all, delete-orphan",
        order_by="AIQueryVariant.created_at",
    )
    exposure_gaps: Mapped[list["ExposureGap"]] = relationship(back_populates="query_target")
    exposure_actions: Mapped[list["ExposureAction"]] = relationship(back_populates="query_target")


class AIQueryVariant(Base):
    __tablename__ = "ai_query_variants"
    __table_args__ = (
        UniqueConstraint(
            "query_target_id",
            "query_text",
            "platform",
            "language",
            name="uq_ai_query_variants_target_text_platform_language",
        ),
        Index("ix_ai_query_variants_query_target_id", "query_target_id"),
        Index("ix_ai_query_variants_query_matrix_id", "query_matrix_id"),
        Index(
            "ix_ai_query_variants_target_active_platform",
            "query_target_id",
            "is_active",
            "platform",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_query_targets.id", ondelete="CASCADE"),
        nullable=False,
    )
    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), default="CHATGPT", nullable=False)
    language: Mapped[str] = mapped_column(String(20), default="ko", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    query_matrix_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_matrix.id", ondelete="SET NULL"),
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    query_target: Mapped["AIQueryTarget"] = relationship(back_populates="variants")
    query_matrix: Mapped["QueryMatrix | None"] = relationship(back_populates="query_variants")


class MeasurementRun(Base):
    __tablename__ = "measurement_runs"
    __table_args__ = (
        Index("ix_measurement_runs_hospital_created", "hospital_id", "created_at"),
        Index("ix_measurement_runs_hospital_status", "hospital_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_label: Mapped[str | None] = mapped_column(String(200))
    measurement_method: Mapped[str] = mapped_column(
        String(50),
        default="OPENAI_RESPONSE",
        server_default="OPENAI_RESPONSE",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="PENDING",
        server_default="PENDING",
        nullable=False,
    )
    query_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_name: Mapped[str | None] = mapped_column(String(100))
    search_mode: Mapped[str | None] = mapped_column(String(50))
    config: Mapped[dict | None] = mapped_column(JSON)
    error_summary: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    hospital: Mapped["Hospital"] = relationship(back_populates="measurement_runs")
    sov_records: Mapped[list["SovRecord"]] = relationship(back_populates="measurement_run")


class SovRecord(Base):
    __tablename__ = "sov_records"
    __table_args__ = (
        Index("ix_sov_records_measurement_run_id", "measurement_run_id"),
        Index("ix_sov_records_ai_query_target_id", "ai_query_target_id"),
        Index("ix_sov_records_ai_query_variant_id", "ai_query_variant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))
    query_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("query_matrix.id", ondelete="CASCADE"))
    measurement_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("measurement_runs.id", ondelete="SET NULL"),
    )
    ai_query_target_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_query_targets.id", ondelete="SET NULL"),
    )
    ai_query_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_query_variants.id", ondelete="SET NULL"),
    )

    ai_platform: Mapped[str] = mapped_column(String(50))   # chatgpt | gemini
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_mentioned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mention_rank: Mapped[int | None] = mapped_column(Integer)
    mention_sentiment: Mapped[str | None] = mapped_column(String(20))
    mention_context: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)
    competitor_mentions: Mapped[list | None] = mapped_column(JSON)
    measurement_method: Mapped[str | None] = mapped_column(
        String(50),
        default="OPENAI_RESPONSE",
        server_default="OPENAI_RESPONSE",
    )
    measurement_status: Mapped[str | None] = mapped_column(
        String(50),
        default="SUCCESS",
        server_default="SUCCESS",
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    source_urls: Mapped[list | None] = mapped_column(JSON)
    # 예: [{"name": "경쟁병원", "is_mentioned": true, "mention_rank": 2}]

    hospital: Mapped["Hospital"] = relationship(back_populates="sov_records")
    query: Mapped["QueryMatrix"] = relationship(back_populates="sov_records")
    measurement_run: Mapped["MeasurementRun | None"] = relationship(back_populates="sov_records")
    ai_query_target: Mapped["AIQueryTarget | None"] = relationship()
    ai_query_variant: Mapped["AIQueryVariant | None"] = relationship()


class ExposureGap(Base):
    __tablename__ = "exposure_gaps"
    __table_args__ = (
        Index("ix_exposure_gaps_hospital_status", "hospital_id", "status"),
        Index("ix_exposure_gaps_query_target_status", "query_target_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"),
        nullable=False,
    )
    query_target_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_query_targets.id", ondelete="SET NULL"),
    )
    gap_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM", nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    diagnosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(30), default="OPEN", nullable=False)

    hospital: Mapped["Hospital"] = relationship(back_populates="exposure_gaps")
    query_target: Mapped["AIQueryTarget | None"] = relationship(back_populates="exposure_gaps")
    actions: Mapped[list["ExposureAction"]] = relationship(back_populates="gap")


class ExposureAction(Base):
    __tablename__ = "exposure_actions"
    __table_args__ = (
        Index("ix_exposure_actions_hospital_status_due_month", "hospital_id", "status", "due_month"),
        Index("ix_exposure_actions_query_target_status", "query_target_id", "status"),
        Index("ix_exposure_actions_gap_id", "gap_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"),
        nullable=False,
    )
    query_target_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_query_targets.id", ondelete="SET NULL"),
    )
    gap_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exposure_gaps.id", ondelete="SET NULL"),
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(100))
    due_month: Mapped[str | None] = mapped_column(String(7))
    status: Mapped[str] = mapped_column(String(30), default="OPEN", nullable=False)
    linked_content_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="SET NULL"),
    )
    linked_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("monthly_reports.id", ondelete="SET NULL"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    hospital: Mapped["Hospital"] = relationship(back_populates="exposure_actions")
    query_target: Mapped["AIQueryTarget | None"] = relationship(back_populates="exposure_actions")
    gap: Mapped["ExposureGap | None"] = relationship(back_populates="actions")
    linked_content: Mapped["ContentItem | None"] = relationship(foreign_keys=[linked_content_id])
    linked_report: Mapped["MonthlyReport | None"] = relationship()
