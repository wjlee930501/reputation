import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Plan(str, enum.Enum):
    PLAN_16 = "PLAN_16"   # 월 16편
    PLAN_12 = "PLAN_12"   # 월 12편
    PLAN_8 = "PLAN_8"     # 월 8편


class HospitalStatus(str, enum.Enum):
    ONBOARDING = "ONBOARDING"     # 프로파일 입력 중
    ANALYZING = "ANALYZING"       # V0 분석 중
    BUILDING = "BUILDING"         # 사이트 빌드 중
    PENDING_DOMAIN = "PENDING_DOMAIN"  # 도메인 연결 대기
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
    aeo_domain: Mapped[str | None] = mapped_column(String(200))  # 연결된 AEO 도메인
    aeo_site_path: Mapped[str | None] = mapped_column(String(500))  # 빌드된 사이트 경로

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
    sov_records: Mapped[list["SovRecord"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    monthly_reports: Mapped[list["MonthlyReport"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Hospital {self.name} [{self.status}]>"
