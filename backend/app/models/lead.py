import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SalesLead(Base):
    __tablename__ = "sales_leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clinic_name: Mapped[str] = mapped_column(String(200), nullable=False)
    clinic_type: Mapped[str] = mapped_column(String(200), nullable=False)
    contact: Mapped[str] = mapped_column(String(200), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    privacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_path: Mapped[str | None] = mapped_column(String(500))

    # 개인정보보호법 제15조 / 제21조 — 동의 trail + 보관기간 + 자동 파기
    consent_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    consent_ip: Mapped[str | None] = mapped_column(
        INET().with_variant(String(64), "sqlite"),
        nullable=True,
    )
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(40), nullable=False, default="NEW", server_default="NEW")
    converted_hospital_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conversion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    notification_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notification_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
