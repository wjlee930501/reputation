"""
Admin API — 콘텐츠 스케줄 + 발행
POST   /admin/hospitals/{id}/schedule           — 스케줄 설정
GET    /admin/hospitals/{id}/content            — 콘텐츠 목록 (월별)
GET    /admin/hospitals/{id}/content/{cid}      — 상세 조회
POST   /admin/hospitals/{id}/content/{cid}/publish  — 발행
POST   /admin/hospitals/{id}/content/{cid}/reject   — 반려
"""
import logging
import uuid
from datetime import date, datetime, timezone

import arrow

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.content import (
    ContentItem, ContentSchedule, ContentStatus, ContentType, PLAN_DISTRIBUTION
)
from app.models.hospital import Hospital, HospitalStatus
from app.schemas.content import ContentItemDetail, ContentItemResponse
from app.services import notifier
from app.services.site_builder import build_content_page

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Content"])


class ScheduleCreate(BaseModel):
    plan: str = Field(pattern=r"^PLAN_(16|12|8)$")  # PLAN_16 | PLAN_12 | PLAN_8
    publish_days: list[int]                           # [1, 4] = 화·금
    active_from: date

    @field_validator("publish_days")
    @classmethod
    def validate_publish_days(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("publish_days must not be empty")
        for day in v:
            if day not in range(7):
                raise ValueError(f"Invalid day: {day}. Must be 0-6 (월-일)")
        return list(set(v))  # 중복 제거


class PublishBody(BaseModel):
    published_by: str = Field(default="AE", max_length=100)  # AE 이름


@router.post("/{hospital_id}/schedule", status_code=status.HTTP_201_CREATED)
async def set_schedule(
    hospital_id: uuid.UUID,
    body: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    콘텐츠 스케줄 설정.
    저장 즉시 해당 월의 ContentItem 슬롯을 자동 생성.
    """
    hospital = await _get_hospital(db, hospital_id)

    # 기존 스케줄 비활성화
    old_stmt = select(ContentSchedule).where(
        ContentSchedule.hospital_id == hospital_id,
        ContentSchedule.is_active == True,
    )
    old_result = await db.execute(old_stmt)
    for old in old_result.scalars().all():
        old.is_active = False

    schedule = ContentSchedule(
        hospital_id=hospital_id,
        plan=body.plan,
        publish_days=body.publish_days,
        active_from=body.active_from,
    )
    db.add(schedule)
    await db.flush()

    # 이번 달 콘텐츠 슬롯 자동 생성
    target_month = arrow.get(body.active_from).floor("month")
    slots = _generate_monthly_slots(schedule, target_month, body.plan)

    for slot_date, ctype, seq_no, total in slots:
        db.add(ContentItem(
            hospital_id=hospital_id,
            schedule_id=schedule.id,
            content_type=ctype,
            sequence_no=seq_no,
            total_count=total,
            scheduled_date=slot_date,
            status=ContentStatus.DRAFT,
        ))

    hospital.schedule_set = True
    if hospital.site_live:
        hospital.status = HospitalStatus.ACTIVE

    await db.commit()
    await db.refresh(schedule)

    return {
        "schedule_id": str(schedule.id),
        "plan": body.plan,
        "publish_days": body.publish_days,
        "slots_created": len(slots),
        "first_publish_date": str(slots[0][0]) if slots else None,
    }


@router.get("/{hospital_id}/content", response_model=list[ContentItemResponse])
async def list_content(
    hospital_id: uuid.UUID,
    year: int = Query(default=None),
    month: int = Query(default=None),
    status_filter: str = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    """월별 콘텐츠 목록"""
    now = arrow.now("Asia/Seoul")
    year = year or now.year
    month = month or now.month

    period_start = arrow.Arrow(year, month, 1).date()
    period_end = arrow.Arrow(year, month, 1).ceil("month").date()

    stmt = select(ContentItem).where(
        ContentItem.hospital_id == hospital_id,
        ContentItem.scheduled_date >= period_start,
        ContentItem.scheduled_date <= period_end,
    ).order_by(ContentItem.scheduled_date)

    if status_filter:
        stmt = stmt.where(ContentItem.status == status_filter)

    result = await db.execute(stmt)
    items = result.scalars().all()

    return [_serialize_item(i) for i in items]


@router.get("/{hospital_id}/content/{content_id}", response_model=ContentItemDetail)
async def get_content(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """콘텐츠 상세 (본문 포함)"""
    item = await _get_content(db, content_id, hospital_id)
    return _serialize_item(item, full=True)


@router.post("/{hospital_id}/content/{content_id}/publish")
async def publish_content(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    body: PublishBody,
    db: AsyncSession = Depends(get_db),
):
    """
    AE가 검토 후 [발행] 클릭.
    AEO 홈페이지에 즉시 게재.
    """
    item = await _get_content(db, content_id, hospital_id)
    hospital = await _get_hospital(db, hospital_id)

    if item.status == ContentStatus.PUBLISHED:
        raise HTTPException(status_code=400, detail="Already published")
    if not item.body:
        raise HTTPException(status_code=400, detail="Content not generated yet")

    item.status = ContentStatus.PUBLISHED
    item.published_at = datetime.now(timezone.utc)
    item.published_by = body.published_by
    await db.commit()

    # AEO 사이트에 콘텐츠 페이지 즉시 생성
    try:
        build_content_page(hospital, _serialize_item(item, full=True))
    except Exception as e:
        logger.error(f"Site build failed for content {content_id}: {e}")  # 🔴 CRITICAL fix: was `pass`

    # Slack 알림
    await notifier.notify_content_published(hospital.name, item.title or "")

    return {"detail": "Published", "published_at": item.published_at.isoformat()}


@router.post("/{hospital_id}/content/{content_id}/reject")
async def reject_content(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """반려 — 야간 재생성 큐에 다시 들어감"""
    item = await _get_content(db, content_id, hospital_id)
    item.status = ContentStatus.REJECTED
    item.body = None   # 초기화 → 야간 생성 태스크가 다시 처리
    item.title = None
    item.image_url = None
    await db.commit()
    return {"detail": "Rejected. Will be regenerated tonight."}


# ── 헬퍼 ─────────────────────────────────────────────────────────
def _generate_monthly_slots(
    schedule: ContentSchedule, target_month: arrow.Arrow, plan: str
) -> list[tuple[date, ContentType, int, int]]:
    """(발행일, 콘텐츠유형, 순번, 총편수) 목록 생성"""
    distribution = PLAN_DISTRIBUTION.get(plan, {})
    type_sequence = []
    for ctype, count in distribution.items():
        type_sequence.extend([ctype] * count)
    total = len(type_sequence)

    # 해당 월의 발행 요일 날짜 목록
    dates = []
    day = target_month.floor("month")
    end = target_month.ceil("month")
    while day <= end:
        if day.weekday() in schedule.publish_days:
            dates.append(day.date())
        day = day.shift(days=1)

    return [
        (pub_date, ctype, i + 1, total)
        for i, (pub_date, ctype) in enumerate(zip(dates, type_sequence))
    ]


async def _get_hospital(db, hospital_id) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


async def _get_content(db, content_id, hospital_id) -> ContentItem:
    item = await db.get(ContentItem, content_id)
    if not item or item.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Content not found")
    return item


def _serialize_item(item: ContentItem, full: bool = False) -> dict:
    d = {
        "id": str(item.id),
        "content_type": item.content_type,
        "sequence_no": item.sequence_no,
        "total_count": item.total_count,
        "title": item.title,
        "meta_description": item.meta_description,
        "image_url": item.image_url,
        "scheduled_date": str(item.scheduled_date),
        "status": item.status,
        "generated_at": item.generated_at.isoformat() if item.generated_at else None,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "published_by": item.published_by,
    }
    if full:
        d["body"] = item.body
        d["image_prompt"] = item.image_prompt
    return d
