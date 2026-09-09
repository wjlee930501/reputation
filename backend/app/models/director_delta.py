"""Additive director feedback; retirement is always an explicit human action."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.essence import _jsonb_type


class DirectorDeltaStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class DirectorDeltaSource(str, enum.Enum):
    ADMIN = "ADMIN"
    DIRECTOR = "DIRECTOR"  # 원장 feedback


class DirectorDelta(Base):
    __tablename__ = "director_deltas"
    __table_args__ = (
        Index("ix_director_deltas_hospital_status_created", "hospital_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False
    )
    avoid_messages: Mapped[list] = mapped_column(
        _jsonb_type(), default=list, server_default=text("'[]'"), nullable=False
    )
    prefer_topics: Mapped[list] = mapped_column(
        _jsonb_type(), default=list, server_default=text("'[]'"), nullable=False
    )
    prefer_messages: Mapped[list] = mapped_column(
        _jsonb_type(), default=list, server_default=text("'[]'"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[DirectorDeltaStatus] = mapped_column(
        Enum(
            DirectorDeltaStatus,
            native_enum=False,
            create_constraint=True,
            name="director_delta_status",
        ),
        default=DirectorDeltaStatus.ACTIVE,
        server_default="ACTIVE",
        nullable=False,
    )
    source: Mapped[DirectorDeltaSource] = mapped_column(
        Enum(
            DirectorDeltaSource,
            native_enum=False,
            create_constraint=True,
            name="director_delta_source",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
