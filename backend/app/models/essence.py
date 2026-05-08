import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.content import ContentItem
    from app.models.hospital import Hospital


def _jsonb_type():
    return JSON().with_variant(JSONB, "postgresql")


class SourceType(str, enum.Enum):
    # 텍스트 자료 (URL or raw text)
    NAVER_BLOG = "NAVER_BLOG"
    YOUTUBE = "YOUTUBE"
    HOMEPAGE = "HOMEPAGE"
    INTERVIEW = "INTERVIEW"
    LANDING_PAGE = "LANDING_PAGE"
    BROCHURE = "BROCHURE"
    INTERNAL_NOTE = "INTERNAL_NOTE"
    # 이미지 자료 — file_url 사용. is_public=true 시 /site 공개 표면에 자동 노출.
    # 의료광고법 우려가 큰 환자 후기/Before-After는 의도적으로 enum에 포함하지 않음.
    PHOTO_DOCTOR = "PHOTO_DOCTOR"
    PHOTO_CLINIC_EXTERIOR = "PHOTO_CLINIC_EXTERIOR"
    PHOTO_CLINIC_INTERIOR = "PHOTO_CLINIC_INTERIOR"
    PHOTO_TREATMENT_ROOM = "PHOTO_TREATMENT_ROOM"
    OTHER = "OTHER"


PHOTO_SOURCE_TYPES = frozenset(
    {
        SourceType.PHOTO_DOCTOR,
        SourceType.PHOTO_CLINIC_EXTERIOR,
        SourceType.PHOTO_CLINIC_INTERIOR,
        SourceType.PHOTO_TREATMENT_ROOM,
    }
)


class SourceStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    EXCLUDED = "EXCLUDED"
    ERROR = "ERROR"


class EvidenceNoteType(str, enum.Enum):
    KEY_MESSAGE = "KEY_MESSAGE"
    TONE_SIGNAL = "TONE_SIGNAL"
    TREATMENT_SIGNAL = "TREATMENT_SIGNAL"
    RISK_SIGNAL = "RISK_SIGNAL"
    PATIENT_PROMISE = "PATIENT_PROMISE"
    DOCTOR_PHILOSOPHY = "DOCTOR_PHILOSOPHY"
    LOCAL_CONTEXT = "LOCAL_CONTEXT"
    PROOF_POINT = "PROOF_POINT"
    CONFLICT = "CONFLICT"


class PhilosophyStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"


class HospitalSourceAsset(Base):
    __tablename__ = "hospital_source_assets"
    __table_args__ = (
        Index("ix_hospital_source_assets_hospital_status", "hospital_id", "status"),
        Index("ix_hospital_source_assets_hospital_type", "hospital_id", "source_type"),
        Index("ix_hospital_source_assets_hospital_hash", "hospital_id", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False)

    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType, name="hospital_source_type"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1000))
    raw_text: Mapped[str | None] = mapped_column(Text)
    operator_note: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict] = mapped_column(_jsonb_type(), default=dict, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))

    # 파일 업로드 자산 (이미지/PDF/DOCX). PHOTO_* 타입은 file_url 필수.
    file_url: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)

    # AE가 검수한 사진을 /site 공개 표면에 노출할지. PHOTO_* 타입에만 의미 있음.
    is_public: Mapped[bool] = mapped_column(default=False, nullable=False, server_default=text("false"))

    status: Mapped[SourceStatus] = mapped_column(
        Enum(SourceStatus, name="hospital_source_status"),
        default=SourceStatus.PENDING,
        nullable=False,
    )
    process_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(100))
    updated_by: Mapped[str | None] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    hospital: Mapped["Hospital"] = relationship(back_populates="source_assets")
    evidence_notes: Mapped[list["HospitalSourceEvidenceNote"]] = relationship(
        back_populates="source_asset",
        cascade="all, delete-orphan",
    )


class HospitalSourceEvidenceNote(Base):
    __tablename__ = "hospital_source_evidence_notes"
    __table_args__ = (
        Index("ix_hospital_source_evidence_notes_hospital_type", "hospital_id", "note_type"),
        Index("ix_hospital_source_evidence_notes_source_asset", "source_asset_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False)
    source_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_source_assets.id", ondelete="CASCADE"),
        nullable=False,
    )

    note_type: Mapped[EvidenceNoteType] = mapped_column(
        Enum(EvidenceNoteType, name="hospital_evidence_note_type"),
        nullable=False,
    )
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_start: Mapped[int | None] = mapped_column(Integer)
    excerpt_end: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    note_metadata: Mapped[dict] = mapped_column(_jsonb_type(), default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hospital: Mapped["Hospital"] = relationship(back_populates="evidence_notes")
    source_asset: Mapped["HospitalSourceAsset"] = relationship(back_populates="evidence_notes")


class HospitalContentPhilosophy(Base):
    __tablename__ = "hospital_content_philosophies"
    __table_args__ = (
        Index("ix_hospital_content_philosophies_hospital_status", "hospital_id", "status"),
        Index("ix_hospital_content_philosophies_hospital_version", "hospital_id", "version", unique=True),
        Index(
            "uq_hospital_content_philosophies_one_approved",
            "hospital_id",
            unique=True,
            postgresql_where=text("status = 'APPROVED'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False)

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PhilosophyStatus] = mapped_column(
        Enum(PhilosophyStatus, name="hospital_philosophy_status"),
        default=PhilosophyStatus.DRAFT,
        nullable=False,
    )

    positioning_statement: Mapped[str | None] = mapped_column(Text)
    doctor_voice: Mapped[str | None] = mapped_column(Text)
    patient_promise: Mapped[str | None] = mapped_column(Text)
    content_principles: Mapped[list] = mapped_column(_jsonb_type(), default=list, nullable=False)
    tone_guidelines: Mapped[list] = mapped_column(_jsonb_type(), default=list, nullable=False)
    must_use_messages: Mapped[list] = mapped_column(_jsonb_type(), default=list, nullable=False)
    avoid_messages: Mapped[list] = mapped_column(_jsonb_type(), default=list, nullable=False)
    treatment_narratives: Mapped[list] = mapped_column(_jsonb_type(), default=list, nullable=False)
    local_context: Mapped[dict] = mapped_column(_jsonb_type(), default=dict, nullable=False)
    medical_ad_risk_rules: Mapped[list] = mapped_column(_jsonb_type(), default=list, nullable=False)
    evidence_map: Mapped[dict] = mapped_column(_jsonb_type(), default=dict, nullable=False)
    source_asset_ids: Mapped[list] = mapped_column(_jsonb_type(), default=list, nullable=False)
    unsupported_gaps: Mapped[list] = mapped_column(_jsonb_type(), default=list, nullable=False)
    conflict_notes: Mapped[list] = mapped_column(_jsonb_type(), default=list, nullable=False)
    synthesis_notes: Mapped[str | None] = mapped_column(Text)
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(64))

    created_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    hospital: Mapped["Hospital"] = relationship(back_populates="content_philosophies")
    content_items: Mapped[list["ContentItem"]] = relationship(back_populates="content_philosophy")
