import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.content import ContentItem, ContentSchedule
    from app.models.essence import (
        HospitalContentPhilosophy,
        HospitalSourceAsset,
        HospitalSourceEvidenceNote,
    )
    from app.models.report import MonthlyReport
    from app.models.sov import (
        AIQueryTarget,
        ExposureAction,
        ExposureGap,
        MeasurementRun,
        QueryMatrix,
        SovRecord,
    )


class Plan(str, enum.Enum):
    PLAN_16 = "PLAN_16"   # 월 16편
    PLAN_12 = "PLAN_12"   # 월 12편
    PLAN_8 = "PLAN_8"     # 월 8편


class HospitalStatus(str, enum.Enum):
    ONBOARDING = "ONBOARDING"     # 프로파일 입력 중
    ANALYZING = "ANALYZING"       # V0 분석 중
    BUILDING = "BUILDING"         # 콘텐츠 허브 노출 준비 중
    PENDING_DOMAIN = "PENDING_DOMAIN"  # 공개 도메인 확인 대기
    ACTIVE = "ACTIVE"             # 운영 중
    PAUSED = "PAUSED"             # 일시 정지


class Hospital(Base):
    __tablename__ = "hospitals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[HospitalStatus] = mapped_column(
        Enum(HospitalStatus), default=HospitalStatus.ONBOARDING
    )
    plan: Mapped[Plan | None] = mapped_column(Enum(Plan))

    # ── 연락처 ──────────────────────────────────────────────────────
    address: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(50))
    business_hours: Mapped[dict | None] = mapped_column(JSON)
    # 예: {"mon": "09:00-18:00", "tue": "09:00-18:00", ..., "sat": "09:00-13:00", "sun": "휴진"}

    # ── URL 자산 ─────────────────────────────────────────────────────
    website_url: Mapped[str | None] = mapped_column(String(500))
    blog_url: Mapped[str | None] = mapped_column(String(500))
    kakao_channel_url: Mapped[str | None] = mapped_column(String(500))
    google_business_profile_url: Mapped[str | None] = mapped_column(String(500))
    google_maps_url: Mapped[str | None] = mapped_column(String(500))
    naver_place_url: Mapped[str | None] = mapped_column(String(500))
    aeo_domain: Mapped[str | None] = mapped_column(String(200))  # 연결된 AEO 도메인
    aeo_site_path: Mapped[str | None] = mapped_column(String(500))  # 빌드된 사이트 경로
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    # ── 엔티티 식별자 (sameAs / entity grounding) ────────────────────
    # AI 답변 인용은 엔티티 합의에서 시작 — 백링크 0.10 vs 브랜드 멘션 0.66 상관.
    # Wikidata Q-ID는 LLM Knowledge Graph의 안정 식별자이며, GBP/Naver/Kakao는
    # 한국 환자가 실제로 보는 로컬 anchor. HIRA는 한국 의료기관 준공식 권위 데이터.
    wikidata_qid: Mapped[str | None] = mapped_column(String(50))      # 예: "Q12345678"
    gbp_place_id: Mapped[str | None] = mapped_column(String(255))     # Google Place ID
    naver_place_id: Mapped[str | None] = mapped_column(String(100))   # Naver Place ID (숫자)
    kakao_place_id: Mapped[str | None] = mapped_column(String(100))   # Kakao Place ID
    hira_org_id: Mapped[str | None] = mapped_column(String(50))       # HIRA 요양기관 기호

    # ── 타겟 파라미터 ────────────────────────────────────────────────
    region: Mapped[list] = mapped_column(JSON, default=list)         # ["수원시", "영통구"]
    specialties: Mapped[list] = mapped_column(JSON, default=list)    # ["대장항문외과"]
    keywords: Mapped[list] = mapped_column(JSON, default=list)       # ["치질", "치루", "치열"]
    competitors: Mapped[list] = mapped_column(JSON, default=list)    # ["경쟁병원명"]

    # ── 원장 정보 ────────────────────────────────────────────────────
    director_name: Mapped[str | None] = mapped_column(String(100))
    director_career: Mapped[str | None] = mapped_column(Text)       # 약력 (마크다운)
    director_philosophy: Mapped[str | None] = mapped_column(Text)   # 진료 철학
    director_photo_url: Mapped[str | None] = mapped_column(String(500))
    # Physician schema의 hasCredential / alumniOf / memberOf 매핑용 구조화 자격 정보.
    # 형식: {
    #   "medical_school": "서울대학교 의과대학",
    #   "board_certifications": ["대장항문외과 전문의"],
    #   "society_memberships": ["대한대장항문학회"],
    #   "license_number": "12345"  # 의사면허번호 (선택, 공개 노출 X)
    # }
    director_credentials: Mapped[dict | None] = mapped_column(JSON)

    # ── 진료 항목 ────────────────────────────────────────────────────
    treatments: Mapped[list] = mapped_column(JSON, default=list)
    # 예: [{"name": "치질 수술", "description": "..."}]

    # ── 진행 상태 플래그 ─────────────────────────────────────────────
    profile_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    v0_report_done: Mapped[bool] = mapped_column(Boolean, default=False)
    site_built: Mapped[bool] = mapped_column(Boolean, default=False)
    site_live: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_set: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── 타임스탬프 ───────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # ── Relations ────────────────────────────────────────────────────
    content_schedules: Mapped[list["ContentSchedule"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    content_items: Mapped[list["ContentItem"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    query_matrix: Mapped[list["QueryMatrix"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    query_targets: Mapped[list["AIQueryTarget"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    exposure_gaps: Mapped[list["ExposureGap"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    exposure_actions: Mapped[list["ExposureAction"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    measurement_runs: Mapped[list["MeasurementRun"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    sov_records: Mapped[list["SovRecord"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    monthly_reports: Mapped[list["MonthlyReport"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    source_assets: Mapped[list["HospitalSourceAsset"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    evidence_notes: Mapped[list["HospitalSourceEvidenceNote"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    content_philosophies: Mapped[list["HospitalContentPhilosophy"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Hospital {self.name} [{self.status}]>"
