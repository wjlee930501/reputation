"""병원별 콘텐츠 주력 주제 — Admin 콘텐츠 화면의 '콘텐츠 방향' 카드.

마케터가 원장님이 원하는 진료 항목(예: 11~12월 내시경)을 주력 주제로 저장하면, 아직 주제가 정해지지
않은 글부터 그 주제의 환자 질문을 먼저 골라 쓴다(`services/content_focus.py`, `content_target_planner`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import arrow
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.models.sov import AIQueryTarget
from app.services.audit_log import default_actor, write_audit_log
from app.services.content_focus import (
    MAX_TOPICS,
    SHARE_CHOICES,
    ContentFocus,
    ContentFocusError,
    focus_quota,
    parse_content_focus,
    target_matches_topic,
    target_subject_is_region,
    validate_content_focus,
)
from app.services.specialty_compatibility import target_fits_hospital
from app.utils.db_locks import acquire_hospital_advisory_lock

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Content Focus"])

KST = ZoneInfo("Asia/Seoul")
EXAMPLE_QUESTIONS = 3


class ContentFocusUpdate(BaseModel):
    topics: list[str] = Field(default_factory=list, max_length=20)
    share: int = 50
    from_month: str | None = None
    until_month: str | None = None


class ContentFocusPreview(BaseModel):
    topics: list[str] = Field(default_factory=list, max_length=20)


async def _hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if hospital is None:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


async def _candidate_targets(db: AsyncSession, hospital: Hospital) -> list[AIQueryTarget]:
    """글 주제 후보가 될 수 있는 측정 질문 — 글 주제를 고르는 곳과 같은 거름을 쓴다."""
    rows = (
        (
            await db.execute(
                select(AIQueryTarget)
                .options(selectinload(AIQueryTarget.variants))
                .where(AIQueryTarget.hospital_id == hospital.id, AIQueryTarget.status == "ACTIVE")
            )
        )
        .scalars()
        .all()
    )
    return [
        target
        for target in rows
        if target_fits_hospital(target, hospital) and not target_subject_is_region(target, hospital)
    ]


def _topic_matches(topics: list[str] | tuple[str, ...], targets: list[AIQueryTarget]) -> list[dict[str, Any]]:
    result = []
    for topic in topics:
        matched = sorted(
            (target for target in targets if target_matches_topic(target, topic)),
            key=lambda target: str(target.name or ""),
        )
        result.append(
            {
                "topic": topic,
                "matching_questions": len(matched),
                "examples": [str(target.name) for target in matched[:EXAMPLE_QUESTIONS]],
            }
        )
    return result


def _kst_today() -> date:
    return datetime.now(KST).date()


async def _month_summaries(db: AsyncSession, hospital: Hospital, focus: ContentFocus) -> list[dict[str, Any]]:
    """이번 달과 다음 달 — 글 수·주력 주제 글 수·목표·아직 주제가 정해지지 않은 글 수."""
    today = _kst_today()
    first = today.replace(day=1)
    months = [first, arrow.get(first).shift(months=1).date()]
    summaries = []
    for month_start in months:
        month_end = arrow.get(month_start).ceil("month").date()
        key = month_start.strftime("%Y-%m")
        items = (
            (
                await db.execute(
                    select(ContentItem).where(
                        ContentItem.hospital_id == hospital.id,
                        ContentItem.scheduled_date >= month_start,
                        ContentItem.scheduled_date <= month_end,
                        ContentItem.status != ContentStatus.CANCELLED,
                    )
                )
            )
            .scalars()
            .all()
        )
        total = len(items)
        topics = set(focus.active_topics(key))
        focused = sum(1 for item in items if item.content_focus_topic in topics)
        open_slots = sum(
            1
            for item in items
            if item.status == ContentStatus.DRAFT
            and not item.title
            and not item.body
            and item.query_target_id is None
            and item.scheduled_date >= today
        )
        summaries.append(
            {
                "month": key,
                "active": focus.applies_to(key),
                "total": total,
                "focused": focused,
                "quota": focus_quota(focus.share, total) if focus.applies_to(key) else 0,
                "open_slots": open_slots,
            }
        )
    return summaries


async def _payload(db: AsyncSession, hospital: Hospital) -> dict[str, Any]:
    focus = parse_content_focus(hospital.content_focus_topics)
    targets = await _candidate_targets(db, hospital)
    return {
        "topics": list(focus.topics),
        "share": focus.share,
        "from_month": focus.from_month,
        "until_month": focus.until_month,
        "legacy_topics": list(focus.legacy_topics),
        "share_choices": list(SHARE_CHOICES),
        "max_topics": MAX_TOPICS,
        "topic_matches": _topic_matches(focus.topics, targets),
        "months": await _month_summaries(db, hospital, focus),
    }


@router.get("/{hospital_id}/content-focus")
async def get_content_focus(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    hospital = await _hospital_or_404(db, hospital_id)
    return await _payload(db, hospital)


@router.post("/{hospital_id}/content-focus/preview")
async def preview_content_focus(
    hospital_id: uuid.UUID,
    body: ContentFocusPreview,
    db: AsyncSession = Depends(get_db),
):
    """저장 전 미리보기 — 적은 주제마다 맞는 환자 질문 수. 아무것도 바꾸지 않는다."""
    hospital = await _hospital_or_404(db, hospital_id)
    topics: list[str] = []
    for value in body.topics:
        topic = " ".join(str(value or "").split())
        if topic and topic not in topics:
            topics.append(topic)
    targets = await _candidate_targets(db, hospital)
    return {"topic_matches": _topic_matches(topics[:MAX_TOPICS], targets)}


@router.patch("/{hospital_id}/content-focus")
async def set_content_focus(
    hospital_id: uuid.UUID,
    body: ContentFocusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """주력 주제 저장. 빈 목록이면 끈다. 이미 주제가 정해진 글은 바꾸지 않는다."""
    await acquire_hospital_advisory_lock(db, hospital_id)
    hospital = await _hospital_or_404(db, hospital_id)
    try:
        focus = validate_content_focus(
            topics=body.topics,
            share=body.share,
            from_month=body.from_month,
            until_month=body.until_month,
        )
    except ContentFocusError as exc:
        raise HTTPException(status_code=422, detail={"code": "CONTENT_FOCUS_INVALID", "message": str(exc)}) from exc
    before = parse_content_focus(hospital.content_focus_topics)
    hospital.content_focus_topics = focus.to_storage()
    await write_audit_log(
        db,
        action="set_content_focus",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="hospital",
        target_id=hospital.id,
        detail={
            "before": before.to_storage() if before.enabled else None,
            "before_legacy": list(before.legacy_topics) or None,
            "after": focus.to_storage() if focus.enabled else None,
        },
    )
    await db.commit()
    await db.refresh(hospital)
    return await _payload(db, hospital)
