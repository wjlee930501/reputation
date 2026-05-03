"""
Admin API — Exposure actions
GET /admin/hospitals/{id}/exposure-actions — deterministic TOP actions
"""
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.sov import ExposureAction
from app.services.exposure_action_engine import (
    ensure_hospital_exposure_actions,
    list_top_exposure_actions,
)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Exposure Actions"])


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


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


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
        "linked_report_id": str(action.linked_report_id) if action.linked_report_id else None,
        "completed_at": _iso_or_none(action.completed_at),
        "created_at": _iso_or_none(action.created_at),
        "updated_at": _iso_or_none(action.updated_at),
        "query_target": _serialize_target(target),
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


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
