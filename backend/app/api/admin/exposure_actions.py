"""
Admin API — Exposure actions
GET   /admin/hospitals/{id}/exposure-actions — deterministic TOP actions
GET   /admin/hospitals/{id}/exposure-actions/{action_id}
PATCH /admin/hospitals/{id}/exposure-actions/{action_id}
POST  /admin/hospitals/{id}/exposure-actions/{action_id}/create-brief
"""
import uuid
from datetime import date, datetime, timezone
from typing import Any

import arrow
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital
from app.models.sov import AIQueryTarget, ExposureAction
from app.services.content_brief import BRIEF_STATUS_DRAFT, build_content_brief
from app.services.exposure_action_engine import (
    ensure_hospital_exposure_actions,
    list_top_exposure_actions,
)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Exposure Actions"])

ACTION_STATUSES = {"OPEN", "IN_PROGRESS", "BLOCKED", "COMPLETED", "CANCELLED", "ARCHIVED"}
BRIEF_CAPABLE_ACTION_TYPES = {"CONTENT", "WEBBLOG_IA", "SOURCE"}


class ExposureActionPatch(BaseModel):
    status: str | None = None
    owner: str | None = Field(default=None, max_length=100)
    due_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    linked_content_id: uuid.UUID | None = None


class CreateBriefBody(BaseModel):
    content_id: uuid.UUID | None = None
    content_type: ContentType = ContentType.FAQ
    scheduled_date: date | None = None
    sequence_no: int | None = Field(default=None, ge=1)
    total_count: int | None = Field(default=None, ge=1)
    brief_approved_by: str | None = Field(default=None, max_length=100)


@router.get("/{hospital_id}/exposure-actions")
async def get_exposure_actions(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=3, ge=1, le=20),
):
    """Return deterministic top exposure actions for a hospital."""
    await _get_hospital_or_404(db, hospital_id)

    await ensure_hospital_exposure_actions(db, hospital_id)
    actions = await list_top_exposure_actions(db, hospital_id, limit=limit)
    return [_serialize_action(action) for action in actions]


@router.get("/{hospital_id}/exposure-actions/{action_id}")
async def get_exposure_action(
    hospital_id: uuid.UUID,
    action_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return one exposure action in the work queue."""
    await _get_hospital_or_404(db, hospital_id)
    action = await _get_action_or_404(db, hospital_id, action_id)
    return _serialize_action(action)


@router.patch("/{hospital_id}/exposure-actions/{action_id}")
async def update_exposure_action(
    hospital_id: uuid.UUID,
    action_id: uuid.UUID,
    body: ExposureActionPatch,
    db: AsyncSession = Depends(get_db),
):
    """Update operator-managed work queue fields for an exposure action."""
    await _get_hospital_or_404(db, hospital_id)
    await _lock_action_for_update(db, hospital_id, action_id)
    action = await _get_action_or_404(db, hospital_id, action_id)
    fields = body.model_fields_set

    if "status" in fields:
        if body.status is None or body.status not in ACTION_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid exposure action status")
        action.status = body.status
        if body.status == "COMPLETED":
            if action.completed_at is None:
                action.completed_at = _utcnow()
        else:
            action.completed_at = None

    if "owner" in fields:
        action.owner = body.owner

    if "due_month" in fields:
        if body.due_month is not None:
            _validate_due_month(body.due_month)
        action.due_month = body.due_month

    if "linked_content_id" in fields:
        await _apply_linked_content_update(db, hospital_id, action, body.linked_content_id)

    await db.commit()
    action = await _get_action_or_404(db, hospital_id, action_id)
    return _serialize_action(action)


@router.post("/{hospital_id}/exposure-actions/{action_id}/create-brief")
async def create_exposure_action_brief(
    hospital_id: uuid.UUID,
    action_id: uuid.UUID,
    body: CreateBriefBody | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Create or attach a query-linked content brief for an exposure action."""
    body = body or CreateBriefBody()
    hospital = await _get_hospital_or_404(db, hospital_id)
    await _lock_action_for_update(db, hospital_id, action_id)
    action = await _get_action_or_404(db, hospital_id, action_id)
    _ensure_brief_capable_action(action)

    item = await _resolve_content_slot_for_brief(db, hospital_id, action, body)
    if item is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No available non-published content slot was found for the action month, "
                "and no active content schedule exists to create one."
            ),
        )

    if _uuid_or_none(getattr(item, "exposure_action_id", None)) not in {None, action.id}:
        raise HTTPException(
            status_code=409,
            detail="Content item is already linked to another exposure action",
        )

    philosophy = await _get_approved_philosophy(db, hospital_id)
    query_target = getattr(action, "query_target", None)

    await _clear_previous_content_link(db, action, item.id)
    item.query_target_id = action.query_target_id
    item.exposure_action_id = action.id
    item.content_brief = build_content_brief(
        hospital=hospital,
        content_item=item,
        query_target=query_target,
        exposure_action=action,
        philosophy=philosophy,
    )
    item.brief_status = BRIEF_STATUS_DRAFT
    item.brief_approved_at = None
    item.brief_approved_by = None
    action.linked_content_id = item.id
    action.linked_content = item

    await db.commit()
    await db.refresh(item)
    action = await _get_action_or_404(db, hospital_id, action_id)

    return {
        "action": _serialize_action(action),
        "content_item": _serialize_content_summary(item),
        "philosophy_gate": _serialize_philosophy_gate(philosophy),
    }


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


async def _get_action_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    action_id: uuid.UUID,
) -> ExposureAction:
    result = await db.execute(
        select(ExposureAction)
        .options(
            selectinload(ExposureAction.query_target).selectinload(AIQueryTarget.variants),
            selectinload(ExposureAction.gap),
            selectinload(ExposureAction.linked_content),
        )
        .where(
            ExposureAction.id == action_id,
            ExposureAction.hospital_id == hospital_id,
        )
    )
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=404, detail="Exposure action not found")
    return action


async def _lock_action_for_update(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    action_id: uuid.UUID,
) -> None:
    """Serialize mutating operations that relink one exposure action to content."""
    if not hasattr(db, "execute"):
        return
    result = await db.execute(
        select(ExposureAction.id)
        .where(
            ExposureAction.id == action_id,
            ExposureAction.hospital_id == hospital_id,
        )
        .with_for_update()
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Exposure action not found")


async def _get_content_item_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
) -> ContentItem:
    item = await db.get(ContentItem, content_id)
    if not item or item.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Content item not found")
    return item


async def _lock_content_item_for_update(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
) -> None:
    """Serialize explicit/existing content slot claims across exposure actions."""
    if not hasattr(db, "execute"):
        return
    result = await db.execute(
        select(ContentItem.id)
        .where(
            ContentItem.id == content_id,
            ContentItem.hospital_id == hospital_id,
        )
        .with_for_update()
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Content item not found")


async def _apply_linked_content_update(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    action: ExposureAction,
    linked_content_id: uuid.UUID | None,
) -> None:
    if linked_content_id is None:
        await _clear_previous_content_link(db, action)
        action.linked_content_id = None
        action.linked_content = None
        return

    _ensure_brief_capable_action(action)
    await _lock_content_item_for_update(db, hospital_id, linked_content_id)
    item = await _get_content_item_or_404(db, hospital_id, linked_content_id)
    if _enum_value(item.status) == ContentStatus.PUBLISHED.value:
        raise HTTPException(
            status_code=409,
            detail="Cannot link a published content item to an exposure action",
        )
    if _uuid_or_none(item.exposure_action_id) not in {None, action.id}:
        raise HTTPException(
            status_code=409,
            detail="Content item is already linked to another exposure action",
        )

    await _clear_previous_content_link(db, action, item.id)
    item.exposure_action_id = action.id
    if action.query_target_id:
        item.query_target_id = action.query_target_id
    action.linked_content_id = item.id
    action.linked_content = item


async def _clear_previous_content_link(
    db: AsyncSession,
    action: ExposureAction,
    replacement_content_id: uuid.UUID | None = None,
) -> None:
    if not action.linked_content_id or action.linked_content_id == replacement_content_id:
        return
    previous = await db.get(ContentItem, action.linked_content_id)
    if previous and previous.exposure_action_id == action.id:
        previous.exposure_action_id = None


async def _resolve_content_slot_for_brief(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    action: ExposureAction,
    body: CreateBriefBody,
) -> ContentItem | None:
    if body.content_id:
        await _lock_content_item_for_update(db, hospital_id, body.content_id)
        item = await _get_content_item_or_404(db, hospital_id, body.content_id)
        if _enum_value(item.status) == ContentStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=409,
                detail="Cannot create a draft brief on published content",
            )
        return item

    if action.linked_content_id:
        await _lock_content_item_for_update(db, hospital_id, action.linked_content_id)
        item = await _get_content_item_or_404(db, hospital_id, action.linked_content_id)
        if _enum_value(item.status) == ContentStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=409,
                detail="Cannot regenerate a draft brief on published linked content",
            )
        return item

    period_start, period_end = _action_month_bounds(action.due_month)
    item = await _find_available_content_slot(db, hospital_id, period_start, period_end)
    if item:
        return item

    schedule = await _get_active_content_schedule(db, hospital_id)
    if schedule is None:
        return None

    return await _create_content_slot(db, hospital_id, schedule, period_start, period_end, body)


async def _find_available_content_slot(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> ContentItem | None:
    result = await db.execute(
        select(ContentItem)
        .where(
            ContentItem.hospital_id == hospital_id,
            ContentItem.scheduled_date >= period_start,
            ContentItem.scheduled_date <= period_end,
            ContentItem.status != ContentStatus.PUBLISHED,
            ContentItem.exposure_action_id.is_(None),
        )
        .order_by(ContentItem.scheduled_date, ContentItem.sequence_no)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return result.scalars().first()


async def _get_active_content_schedule(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> ContentSchedule | None:
    result = await db.execute(
        select(ContentSchedule)
        .where(
            ContentSchedule.hospital_id == hospital_id,
            ContentSchedule.is_active,
        )
        .order_by(ContentSchedule.active_from.desc())
        .with_for_update()
        .limit(1)
    )
    return result.scalars().first()


async def _create_content_slot(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    schedule: ContentSchedule,
    period_start: date,
    period_end: date,
    body: CreateBriefBody,
) -> ContentItem:
    scheduled_date = body.scheduled_date or period_start
    if scheduled_date < period_start or scheduled_date > period_end:
        raise HTTPException(
            status_code=400,
            detail="scheduled_date must be within the exposure action month",
        )

    sequence_no = body.sequence_no or await _next_sequence_no(
        db,
        hospital_id,
        period_start,
        period_end,
    )
    total_count = body.total_count or max(sequence_no, _plan_total(schedule.plan))
    item = ContentItem(
        hospital_id=hospital_id,
        schedule_id=schedule.id,
        content_type=body.content_type,
        sequence_no=sequence_no,
        total_count=total_count,
        scheduled_date=scheduled_date,
        status=ContentStatus.DRAFT,
    )
    db.add(item)
    await db.flush()
    return item


async def _next_sequence_no(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> int:
    result = await db.execute(
        select(ContentItem)
        .where(
            ContentItem.hospital_id == hospital_id,
            ContentItem.scheduled_date >= period_start,
            ContentItem.scheduled_date <= period_end,
        )
        .order_by(ContentItem.sequence_no.desc())
        .limit(1)
    )
    last = result.scalars().first()
    return (last.sequence_no + 1) if last else 1


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


def _action_month_bounds(due_month: str | None) -> tuple[date, date]:
    if due_month:
        start = _validate_due_month(due_month)
    else:
        start = arrow.now("Asia/Seoul").floor("month").date()
    end = arrow.get(start).ceil("month").date()
    return start, end


def _validate_due_month(due_month: str) -> date:
    try:
        year, month = (int(part) for part in due_month.split("-", 1))
        return arrow.Arrow(year, month, 1).date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid exposure action due_month")


def _ensure_brief_capable_action(action: ExposureAction) -> None:
    action_type = str(_enum_value(action.action_type)).upper()
    if action_type not in BRIEF_CAPABLE_ACTION_TYPES:
        raise HTTPException(
            status_code=409,
            detail=(
                "Content brief creation is only available for content-producing exposure "
                "actions (CONTENT, WEBBLOG_IA, SOURCE). Measurement actions should be "
                "handled by running baseline measurement."
            ),
        )


def _plan_total(plan: str | None) -> int:
    if not plan:
        return 1
    try:
        return int(str(plan).split("_", 1)[1])
    except (IndexError, ValueError):
        return 1


def _serialize_action(action: ExposureAction) -> dict[str, Any]:
    gap = getattr(action, "gap", None)
    target = getattr(action, "query_target", None)
    return {
        "id": str(action.id),
        "hospital_id": str(action.hospital_id),
        "query_target_id": str(action.query_target_id) if action.query_target_id else None,
        "gap_id": str(action.gap_id) if action.gap_id else None,
        "gap_type": getattr(gap, "gap_type", None),
        "severity": getattr(gap, "severity", None),
        "evidence": getattr(gap, "evidence", None) or {},
        "action_type": action.action_type,
        "title": action.title,
        "description": action.description,
        "owner": action.owner,
        "due_month": action.due_month,
        "status": action.status,
        "linked_content_id": str(action.linked_content_id) if action.linked_content_id else None,
        "linked_content": _serialize_content_summary(action.linked_content)
        if getattr(action, "linked_content", None)
        else None,
        "linked_report_id": str(action.linked_report_id) if action.linked_report_id else None,
        "completed_at": _iso_or_none(action.completed_at),
        "created_at": _iso_or_none(action.created_at),
        "updated_at": _iso_or_none(action.updated_at),
        "query_target": _serialize_target(target),
    }


def _serialize_content_summary(item: ContentItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "content_type": _enum_value(item.content_type),
        "sequence_no": item.sequence_no,
        "total_count": item.total_count,
        "scheduled_date": str(item.scheduled_date),
        "status": _enum_value(item.status),
        "title": item.title,
        "query_target_id": str(item.query_target_id) if item.query_target_id else None,
        "exposure_action_id": str(item.exposure_action_id) if item.exposure_action_id else None,
        "brief_status": item.brief_status,
        "brief_approved_at": _iso_or_none(item.brief_approved_at),
        "brief_approved_by": item.brief_approved_by,
        "content_brief": item.content_brief,
    }


def _serialize_philosophy_gate(philosophy: HospitalContentPhilosophy | None) -> dict[str, Any]:
    if philosophy:
        return {"has_approved_philosophy": True, "message": None}
    return {
        "has_approved_philosophy": False,
        "message": (
            "Approved content philosophy is missing; keep this brief in review "
            "before approval or publishing."
        ),
    }


def _serialize_target(target: Any) -> dict[str, Any] | None:
    if target is None:
        return None
    return {
        "id": str(target.id),
        "name": target.name,
        "target_intent": target.target_intent,
        "priority": target.priority,
        "status": target.status,
        "target_month": target.target_month,
    }


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
