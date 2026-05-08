import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.hospital import Hospital


def _jsonb_type():
    return JSON().with_variant(JSONB, "postgresql")


class AdminAuditLog(Base):
    """Operator-visible audit trail for customer-impacting Admin actions."""

    __tablename__ = "admin_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id", ondelete="SET NULL"), nullable=True
    )
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="AE")
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[dict | None] = mapped_column(_jsonb_type())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hospital: Mapped["Hospital | None"] = relationship()
