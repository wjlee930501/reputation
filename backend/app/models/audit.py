import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _jsonb_type():
    return JSON().with_variant(JSONB, "postgresql")


class AdminAuditLog(Base):
    """Operator-visible audit trail for customer-impacting Admin actions.

    Append-only at the DB level (migration 0024): no FK to hospitals (so a hospital
    delete cannot mutate audit rows) and a trigger blocks UPDATE/DELETE/TRUNCATE.
    ``hospital_id`` is retained as a historical value even if the hospital is gone.
    """

    __tablename__ = "admin_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="AE")
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[dict | None] = mapped_column(_jsonb_type())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
