import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class QueryMatrix(Base):
    __tablename__ = "query_matrix"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))
    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hospital: Mapped["Hospital"] = relationship(back_populates="query_matrix")
    sov_records: Mapped[list["SovRecord"]] = relationship(
        back_populates="query", cascade="all, delete-orphan"
    )


class SovRecord(Base):
    __tablename__ = "sov_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))
    query_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("query_matrix.id", ondelete="CASCADE"))

    ai_platform: Mapped[str] = mapped_column(String(50))   # chatgpt | perplexity
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_mentioned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mention_rank: Mapped[int | None] = mapped_column(Integer)
    mention_sentiment: Mapped[str | None] = mapped_column(String(20))
    mention_context: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)

    hospital: Mapped["Hospital"] = relationship(back_populates="sov_records")
    query: Mapped["QueryMatrix"] = relationship(back_populates="sov_records")
