import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.essence import HospitalContentPhilosophy
    from app.models.hospital import Hospital
    from app.models.sov import AIQueryTarget, ExposureAction


def _jsonb_type():
    return JSON().with_variant(JSONB, "postgresql")


class ContentType(str, enum.Enum):
    FAQ = "FAQ"             # Q&A 형식
    DISEASE = "DISEASE"     # 질환 가이드
    TREATMENT = "TREATMENT" # 시술·치료 안내
    COLUMN = "COLUMN"       # 원장 칼럼
    HEALTH = "HEALTH"       # 건강 정보
    LOCAL = "LOCAL"         # 지역 특화
    NOTICE = "NOTICE"       # 병원 공지


class ContentStatus(str, enum.Enum):
    DRAFT = "DRAFT"           # 생성 완료 또는 자동 발행 안전검사 대기
    READY = "READY"           # 레거시 호환 상태 (신규 기본 플로우는 DRAFT→PUBLISHED)
    PUBLISHED = "PUBLISHED"   # 발행 완료
    REJECTED = "REJECTED"     # 반려 (재생성 필요)
    CANCELLED = "CANCELLED"   # 중복·오래된 슬롯 종료 (자동 재생성/발행 제외)


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
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"),
        index=True,  # ix_content_schedules_hospital_id (migration 0023)
    )

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
    __table_args__ = (
        Index(
            "uq_content_items_schedule_slot",
            "schedule_id",
            "scheduled_date",
            "sequence_no",
            unique=True,
        ),
        Index("ix_content_items_hospital_status", "hospital_id", "status"),
        Index("ix_content_items_hospital_scheduled", "hospital_id", "scheduled_date"),
        Index("ix_content_items_scheduled_date", "scheduled_date"),
        Index("ix_content_items_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_schedules.id", ondelete="CASCADE"),
        index=True,  # ix_content_items_schedule_id (migration 0023)
    )

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
    content_philosophy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospital_content_philosophies.id", ondelete="SET NULL")
    )
    query_target_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_query_targets.id", ondelete="SET NULL")
    )
    exposure_action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exposure_actions.id", ondelete="SET NULL")
    )
    content_brief: Mapped[dict | None] = mapped_column(_jsonb_type())
    brief_status: Mapped[str | None] = mapped_column(String(30))
    brief_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    brief_approved_by: Mapped[str | None] = mapped_column(String(100))
    essence_status: Mapped[str | None] = mapped_column(String(50))
    essence_check_summary: Mapped[dict | None] = mapped_column(_jsonb_type())

    # 본문 근거 자료 (GEO 신호 — AI 인용 가능성 ↑)
    # list of {"title": str, "url": str}
    references_list: Mapped[list | None] = mapped_column(_jsonb_type())

    # FAQ 전용: FAQPage schema의 Question/Answer로 직접 매핑되는 짧은 형태.
    # 본문(body)에서 분리해 Google FAQ rich result 가이드라인 준수.
    faq_question: Mapped[str | None] = mapped_column(String(300))
    faq_answer_summary: Mapped[str | None] = mapped_column(String(600))

    # 전월 이월 추적 (월말 반려 carry-over): 반려 재스케줄이 원래 발행 예정일과 다른
    # 달로 넘어간 경우 원래 scheduled_date를 기록한다. 야간 생성이 이월분을 최우선
    # 처리하고, 아침 Slack에 "(전월 이월 — 우선 검토)"를 붙이는 근거. 내부 운영 데이터 —
    # 공개(/site) 직렬화에는 포함하지 않는다.
    carried_over_from: Mapped[date | None] = mapped_column(Date)

    # 타임스탬프
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str | None] = mapped_column(String(100))  # AE 이름 또는 SYSTEM_AUTO_PUBLISH
    # 자동 발행과 Slack 후행 확인을 분리한다. 발행 커밋 이후 Slack이 실패해도
    # post_publish_notified_at이 NULL로 남아 다음 작업 재시도에서 복구된다.
    post_publish_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 후행 확인은 공개를 막지 않는 비차단 운영 기록이다.
    post_publish_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_publish_reviewed_by: Mapped[str | None] = mapped_column(String(100))
    body_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hospital: Mapped["Hospital"] = relationship(back_populates="content_items")
    schedule: Mapped["ContentSchedule"] = relationship(back_populates="content_items")
    content_philosophy: Mapped["HospitalContentPhilosophy | None"] = relationship(
        back_populates="content_items"
    )
    query_target: Mapped["AIQueryTarget | None"] = relationship()
    exposure_action: Mapped["ExposureAction | None"] = relationship(
        foreign_keys=[exposure_action_id]
    )
