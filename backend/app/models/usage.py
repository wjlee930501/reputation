import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
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
