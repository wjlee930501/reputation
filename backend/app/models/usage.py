import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HospitalUsageKind(StrEnum):
    ONBOARDING = "onboarding"
    CONTENT = "content"
    IMAGE = "image"
    SOV = "sov"


class HospitalUsageEvent(Base):
    __tablename__ = "hospital_usage_events"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('onboarding', 'content', 'image', 'sov')",
            name="ck_hospital_usage_events_kind",
        ),
        Index(
            "ix_hospital_usage_events_hospital_kind_created",
            "hospital_id",
            "kind",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _json_type():
    return JSON().with_variant(JSONB, "postgresql")


class ProviderCacheStatus(StrEnum):
    HIT = "hit"
    MISS = "miss"
    WRITE = "write"
    BYPASS = "bypass"
    UNKNOWN = "unknown"


class ProviderUsageEvent(Base):
    """Append-only normalized record for one provider HTTP attempt.

    Nullable unit columns deliberately preserve unknown usage. A recorded zero means the
    provider reported zero; ``usage_known=false`` means the response supplied no reliable
    usage payload (including failed HTTP attempts).
    """

    __tablename__ = "provider_usage_events"
    __table_args__ = (
        CheckConstraint(
            "cost_category IN ('content', 'image', 'sov', 'leadgen')",
            name="ck_provider_usage_events_cost_category",
        ),
        CheckConstraint(
            "cache_status IN ('hit', 'miss', 'write', 'bypass', 'unknown')",
            name="ck_provider_usage_events_cache_status",
        ),
        CheckConstraint(
            "hospital_id IS NULL OR lead_id IS NULL",
            name="ck_provider_usage_events_single_owner",
        ),
        CheckConstraint("http_attempt >= 1", name="ck_provider_usage_events_http_attempt"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_provider_usage_events_input_tokens",
        ),
        CheckConstraint(
            "cache_creation_input_tokens IS NULL OR cache_creation_input_tokens >= 0",
            name="ck_provider_usage_events_cache_creation_tokens",
        ),
        CheckConstraint(
            "cache_read_input_tokens IS NULL OR cache_read_input_tokens >= 0",
            name="ck_provider_usage_events_cache_read_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_provider_usage_events_output_tokens",
        ),
        CheckConstraint(
            "reasoning_tokens IS NULL OR reasoning_tokens >= 0",
            name="ck_provider_usage_events_reasoning_tokens",
        ),
        CheckConstraint(
            "search_units IS NULL OR search_units >= 0",
            name="ck_provider_usage_events_search_units",
        ),
        CheckConstraint(
            "image_units IS NULL OR image_units >= 0",
            name="ck_provider_usage_events_image_units",
        ),
        Index(
            "ix_provider_usage_events_hospital_workflow_created",
            "hospital_id",
            "workflow",
            "created_at",
        ),
        Index(
            "ix_provider_usage_events_lead_workflow_created",
            "lead_id",
            "workflow",
            "created_at",
        ),
        Index(
            "ix_provider_usage_events_run_item_attempt",
            "run_id",
            "item_id",
            "logical_call_id",
            "http_attempt",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(String(160))
    workflow: Mapped[str] = mapped_column(String(80), nullable=False)
    cost_category: Mapped[str] = mapped_column(String(20), nullable=False)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sales_leads.id", ondelete="SET NULL"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(200))
    item_id: Mapped[str | None] = mapped_column(String(200))
    attempt_id: Mapped[str | None] = mapped_column(String(200))
    logical_call_id: Mapped[str | None] = mapped_column(String(200))
    http_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str | None] = mapped_column(String(240), unique=True)
    cache_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProviderCacheStatus.UNKNOWN.value,
        server_default=ProviderCacheStatus.UNKNOWN.value,
    )
    usage_known: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_creation_input_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    search_units: Mapped[int | None] = mapped_column(Integer)
    image_units: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", _json_type(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
