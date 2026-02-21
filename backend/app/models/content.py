import enum
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ContentType(str, enum.Enum):
    FAQ = "FAQ"             # Q&A 형식
    DISEASE = "DISEASE"     # 질환 가이드
    TREATMENT = "TREATMENT" # 시술·치료 안내
    COLUMN = "COLUMN"       # 원장 칼럼
    HEALTH = "HEALTH"       # 건강 정보
    LOCAL = "LOCAL"         # 지역 특화
    NOTICE = "NOTICE"       # 병원 공지


class ContentStatus(str, enum.Enum):
    DRAFT = "DRAFT"           # 초안 생성 완료 (AE 검토 전)
    READY = "READY"           # AE 검토 완료, 발행 대기 (현재 미사용 — 바로 발행)
    PUBLISHED = "PUBLISHED"   # 발행 완료
    REJECTED = "REJECTED"     # 반려 (재생성 필요)


# 요금제별 유형·편수 배분
PLAN_DISTRIBUTION = {
    "PLAN_16": {
        ContentType.FAQ: 4,
        ContentType.DISEASE: 3,
        ContentType.TREATMENT: 3,
        ContentType.COLUMN: 2,
        ContentType.HEALTH: 2,
        ContentType.LOCAL: 1,
        ContentType.NOTICE: 1,
    },
    "PLAN_12": {
        ContentType.FAQ: 3,
        ContentType.DISEASE: 3,
        ContentType.TREATMENT: 2,
        ContentType.COLUMN: 2,
        ContentType.HEALTH: 1,
        ContentType.LOCAL: 1,
        ContentType.NOTICE: 0,
    },
    "PLAN_8": {
        ContentType.FAQ: 2,
        ContentType.DISEASE: 2,
        ContentType.TREATMENT: 2,
        ContentType.COLUMN: 1,
        ContentType.HEALTH: 1,
        ContentType.LOCAL: 0,
        ContentType.NOTICE: 0,
    },
}


class ContentSchedule(Base):
    """병원별 콘텐츠 발행 스케줄"""
    __tablename__ = "content_schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))

    plan: Mapped[str] = mapped_column(String(20), nullable=False)  # PLAN_16 | PLAN_12 | PLAN_8
    publish_days: Mapped[list] = mapped_column(JSON, nullable=False)
    # 예: [1, 4] = 화요일·금요일 (월=0, 화=1, 수=2, 목=3, 금=4, 토=5, 일=6)

    active_from: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hospital: Mapped["Hospital"] = relationship(back_populates="content_schedules")
    content_items: Mapped[list["ContentItem"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class ContentItem(Base):
    """개별 콘텐츠 아이템"""
    __tablename__ = "content_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_schedules.id", ondelete="CASCADE"))

    content_type: Mapped[ContentType] = mapped_column(Enum(ContentType), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)   # 이번 달 N번째
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)   # 이번 달 전체 편수

    # 콘텐츠 본문
    title: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)          # 마크다운
    meta_description: Mapped[str | None] = mapped_column(String(300))  # SEO용 요약

    # 이미지
    image_url: Mapped[str | None] = mapped_column(String(500))    # GCS public URL
    image_prompt: Mapped[str | None] = mapped_column(Text)        # 생성에 쓴 프롬프트

    # 스케줄·상태
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ContentStatus] = mapped_column(Enum(ContentStatus), default=ContentStatus.DRAFT)

    # 타임스탬프
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str | None] = mapped_column(String(100))  # AE 이름

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hospital: Mapped["Hospital"] = relationship(back_populates="content_items")
    schedule: Mapped["ContentSchedule"] = relationship(back_populates="content_items")
