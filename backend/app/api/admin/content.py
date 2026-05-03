"""
Admin API — 콘텐츠 스케줄 + 발행
POST   /admin/hospitals/{id}/schedule               — 스케줄 설정
GET    /admin/hospitals/{id}/content                — 콘텐츠 목록 (월별)
GET    /admin/hospitals/{id}/content/{cid}          — 상세 조회
PATCH  /admin/hospitals/{id}/content/{cid}          — 제목/본문/meta 수정
PATCH  /admin/hospitals/{id}/content/{cid}/brief    — 타깃 질의/액션/brief 수정
POST   /admin/hospitals/{id}/content/{cid}/publish  — 발행
POST   /admin/hospitals/{id}/content/{cid}/reject   — 반려
"""
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Optional

import arrow
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.content import ContentItem, ContentSchedule, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.sov import AIQueryTarget, ExposureAction
from app.schemas.content import ContentBriefUpdate, ContentItemDetail, ContentItemResponse
from app.services import notifier
from app.services.content_brief import (
    BRIEF_STATUS_APPROVED,
    BRIEF_STATUS_DRAFT,
    BRIEF_STATUSES,
    build_content_brief,
)
from app.services.content_calendar import generate_monthly_slots
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, screen_content_against_philosophy
from app.services.gcs_utils import get_signed_url
from app.utils.medical_filter import check_forbidden

logger = logging.getLogger(__name__)

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


class ContentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    body: str | None = None
    meta_description: str | None = Field(default=None, max_length=500)


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
        ContentSchedule.is_active,
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
    slots = generate_monthly_slots(body.plan, body.publish_days, target_month)

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
    status_filter: Optional[ContentStatus] = Query(default=None, alias="status"),
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


@router.patch("/{hospital_id}/content/{content_id}", response_model=ContentItemDetail)
async def update_content(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    body: ContentPatch,
    db: AsyncSession = Depends(get_db),
):
    """
    제목/본문/meta 수정.
    저장 시 의료광고 금지표현 검사 → 위반 시 400 + 위반 목록 반환.
    """
    item = await _get_content(db, content_id, hospital_id)

    # 금지 표현 검사 (수정 대상 필드만)
    violations: list[str] = []
    new_title = body.title if body.title is not None else item.title
    new_body = body.body if body.body is not None else item.body
    new_meta = body.meta_description if body.meta_description is not None else item.meta_description

    for field_value in [new_title, new_body, new_meta]:
        if field_value:
            violations.extend(check_forbidden(field_value))

    # 중복 제거
    violations = list(dict.fromkeys(violations))

    if violations:
        raise HTTPException(
            status_code=400,
            detail={"message": "의료광고 금지 표현이 포함되어 있습니다.", "violations": violations},
        )

    if body.title is not None:
        item.title = body.title
    if body.body is not None:
        item.body = body.body
    if body.meta_description is not None:
        item.meta_description = body.meta_description

    philosophy = await _get_approved_philosophy(db, hospital_id)
    screening = screen_content_against_philosophy(item, philosophy)
    item.content_philosophy_id = philosophy.id if philosophy else None
    item.essence_status = screening.status
    item.essence_check_summary = screening.summary

    await db.commit()
    await db.refresh(item)
    return _serialize_item(item, full=True)


@router.patch("/{hospital_id}/content/{content_id}/brief", response_model=ContentItemDetail)
async def update_content_brief(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    body: ContentBriefUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update query/action links and the operator-editable content brief."""
    item = await _get_content(db, content_id, hospital_id)
    hospital = await _get_hospital(db, hospital_id)
    await _apply_content_brief_update(db, hospital, item, body)

    await db.commit()
    await db.refresh(item)
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

    full_text = " ".join(part for part in [item.title, item.body, item.meta_description] if part)
    violations = check_forbidden(full_text)
    if violations:
        item.essence_status = "NEEDS_ESSENCE_REVIEW"
        item.essence_check_summary = {
            "blocking": True,
            "findings": [f"의료광고 금지 표현: {', '.join(violations)}"],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail={"message": "의료광고 금지 표현이 포함되어 있어 발행할 수 없습니다.", "violations": violations},
        )

    philosophy = await _get_approved_philosophy(db, hospital_id)
    screening = screen_content_against_philosophy(item, philosophy)
    item.content_philosophy_id = philosophy.id if philosophy else None
    item.essence_status = screening.status
    item.essence_check_summary = screening.summary
    if screening.status != ESSENCE_STATUS_ALIGNED:
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Essence 검수 상태 때문에 발행할 수 없습니다.",
                "essence_status": screening.status,
                "essence_check_summary": screening.summary,
            },
        )

    item.status = ContentStatus.PUBLISHED
    item.published_at = datetime.now(timezone.utc)
    item.published_by = body.published_by
    await db.commit()

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


async def _get_query_target_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    query_target_id: uuid.UUID,
) -> AIQueryTarget:
    result = await db.execute(
        select(AIQueryTarget)
        .options(selectinload(AIQueryTarget.variants))
        .where(
            AIQueryTarget.id == query_target_id,
            AIQueryTarget.hospital_id == hospital_id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=400, detail="query_target_id does not belong to this hospital")
    return target


async def _get_exposure_action_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    exposure_action_id: uuid.UUID,
) -> ExposureAction:
    result = await db.execute(
        select(ExposureAction)
        .options(selectinload(ExposureAction.query_target))
        .where(
            ExposureAction.id == exposure_action_id,
            ExposureAction.hospital_id == hospital_id,
        )
    )
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=400, detail="exposure_action_id does not belong to this hospital")
    return action


async def _get_approved_philosophy(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> HospitalContentPhilosophy | None:
    result = await db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    )
    return result.scalar_one_or_none()


async def _apply_content_brief_update(
    db: AsyncSession,
    hospital: Hospital,
    item: ContentItem,
    body: ContentBriefUpdate,
) -> None:
    fields = body.model_fields_set
    if body.brief_status is not None and body.brief_status not in BRIEF_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid brief_status")

    query_target: AIQueryTarget | None = None
    exposure_action: ExposureAction | None = None
    link_changed = False

    if "exposure_action_id" in fields:
        link_changed = True
        item.exposure_action_id = body.exposure_action_id
        if body.exposure_action_id:
            exposure_action = await _get_exposure_action_or_404(
                db,
                hospital.id,
                body.exposure_action_id,
            )
            exposure_action.linked_content_id = item.id
            if "query_target_id" not in fields and exposure_action.query_target_id:
                item.query_target_id = exposure_action.query_target_id
        else:
            item.exposure_action_id = None
    elif item.exposure_action_id:
        exposure_action = await _get_exposure_action_or_404(db, hospital.id, item.exposure_action_id)

    if "query_target_id" in fields:
        link_changed = True
        item.query_target_id = body.query_target_id
        if body.query_target_id:
            query_target = await _get_query_target_or_404(db, hospital.id, body.query_target_id)
    elif item.query_target_id:
        query_target = await _get_query_target_or_404(db, hospital.id, item.query_target_id)

    if query_target is None and exposure_action and exposure_action.query_target_id:
        query_target = await _get_query_target_or_404(db, hospital.id, exposure_action.query_target_id)
        if item.query_target_id is None:
            item.query_target_id = query_target.id

    if "content_brief" in fields:
        item.content_brief = body.content_brief

    should_regenerate = body.regenerate_brief or (
        link_changed and "content_brief" not in fields and (query_target is not None or exposure_action is not None)
    )
    if should_regenerate:
        philosophy = await _get_approved_philosophy(db, hospital.id)
        item.content_brief = build_content_brief(
            hospital=hospital,
            content_item=item,
            query_target=query_target,
            exposure_action=exposure_action,
            philosophy=philosophy,
        )
        if "brief_status" not in fields:
            item.brief_status = BRIEF_STATUS_DRAFT
            item.brief_approved_at = None
            item.brief_approved_by = None

    if "brief_status" in fields:
        item.brief_status = body.brief_status
        if body.brief_status == BRIEF_STATUS_APPROVED:
            if not item.content_brief:
                raise HTTPException(status_code=400, detail="Cannot approve an empty content brief")
            item.brief_approved_at = datetime.now(timezone.utc)
            item.brief_approved_by = body.brief_approved_by or item.brief_approved_by or "AE"
        else:
            item.brief_approved_at = None
            item.brief_approved_by = None
    elif "brief_approved_by" in fields and item.brief_status == BRIEF_STATUS_APPROVED:
        item.brief_approved_by = body.brief_approved_by


def _serialize_item(item: ContentItem, full: bool = False) -> dict:
    d = {
        "id": str(item.id),
        "content_type": item.content_type,
        "sequence_no": item.sequence_no,
        "total_count": item.total_count,
        "title": item.title,
        "meta_description": item.meta_description,
        "image_url": get_signed_url(item.image_url) if item.image_url else None,
        "scheduled_date": str(item.scheduled_date),
        "status": item.status,
        "generated_at": item.generated_at.isoformat() if item.generated_at else None,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "published_by": item.published_by,
        "content_philosophy_id": str(item.content_philosophy_id) if item.content_philosophy_id else None,
        "query_target_id": str(item.query_target_id) if item.query_target_id else None,
        "exposure_action_id": str(item.exposure_action_id) if item.exposure_action_id else None,
        "content_brief": item.content_brief,
        "brief_status": item.brief_status,
        "brief_approved_at": item.brief_approved_at.isoformat() if item.brief_approved_at else None,
        "brief_approved_by": item.brief_approved_by,
        "essence_status": item.essence_status,
        "essence_check_summary": item.essence_check_summary,
    }
    if full:
        d["body"] = item.body
        d["image_prompt"] = item.image_prompt
    return d
