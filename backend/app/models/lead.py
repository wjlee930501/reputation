import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 리드 출처. 기존 문의 폼은 INQUIRY, 무료 진단 퍼널(1단)은 AI_DIAGNOSIS.
LEAD_SOURCE_INQUIRY = "INQUIRY"
LEAD_SOURCE_AI_DIAGNOSIS = "AI_DIAGNOSIS"

# 도입문의 폼이 clinic_type에 심는 표식. `source`가 없던 시절의 리드를 가려내는 근거이자
# Admin 목록이 도입문의 배지·상세 카드를 띄우는 판정값이라, 나중 단계가 덮어쓰면 안 된다.
LEAD_CLINIC_TYPE_INQUIRY_MARKER = "도입문의"


def is_internal_inquiry(lead: "SalesLead | None") -> bool:
    """Whether a lead report is sales-only and must never enter customer delivery."""
    if lead is None:
        return False
    return (
        (lead.source or "").strip().upper() == LEAD_SOURCE_INQUIRY
        or (lead.clinic_type or "").strip() == LEAD_CLINIC_TYPE_INQUIRY_MARKER
    )


class SalesLead(Base):
    __tablename__ = "sales_leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clinic_name: Mapped[str] = mapped_column(String(200), nullable=False)
    clinic_type: Mapped[str] = mapped_column(String(200), nullable=False)
    contact: Mapped[str] = mapped_column(String(200), nullable=False)
    # AI 진단 신청은 자유 문의가 아니라 폼이므로 문의 내용이 없을 수 있다 (PRD §4-2).
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    privacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_path: Mapped[str | None] = mapped_column(String(500))

    # ── 무료 진단 퍼널 신규 필드 (PRD §4-2)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 병원 대표번호. 담당자 연락처(`contact`)와 **다른 값**이다 — 1회 제한의 병원 측 키.
    clinic_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    region_keyword: Mapped[str | None] = mapped_column(String(100), nullable=True)
    core_keywords: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    source: Mapped[str] = mapped_column(
        String(40), nullable=False, default=LEAD_SOURCE_INQUIRY, server_default=LEAD_SOURCE_INQUIRY
    )

    # 개인정보보호법 제15조 / 제21조 — 동의 trail + 보관기간 + 자동 파기
    consent_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    consent_ip: Mapped[str | None] = mapped_column(
        INET().with_variant(String(64), "sqlite"),
        nullable=True,
    )
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(40), nullable=False, default="NEW", server_default="NEW")
    converted_hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospitals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conversion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    notification_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notification_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
