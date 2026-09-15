"""Audited human conversation preferences; no generation side effects."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.accounts import require_active_account
from app.core.database import get_db
from app.models.admin_user import ROLE_OPERATOR, ROLE_OWNER, AdminUser
from app.models.director_delta import DirectorDelta, DirectorDeltaStatus
from app.models.hospital import Hospital
from app.models.report import MonthlyReport
from app.services.audit_log import write_audit_log
from app.services.director_delta import (
    ActiveDirectorDeltaLimit,
    DirectorDeltaInput,
    create_director_delta,
    retire_director_delta,
)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Director feedback"])


class FeedbackInput(DirectorDeltaInput):
    report_id: uuid.UUID | None = None
    conversation_reference: str | None = Field(default=None, max_length=500)


async def _hospital(db, hospital_id, actor):
    if actor.role not in (ROLE_OPERATOR, ROLE_OWNER):
        raise HTTPException(403, "병원 운영 권한이 필요합니다.")
    if await db.get(Hospital, hospital_id) is None:
        raise HTTPException(404, "Hospital not found")


def _payload(delta):
    return {
        key: getattr(delta, key)
        for key in (
            "id",
            "hospital_id",
            "source",
            "status",
            "avoid_messages",
            "prefer_topics",
            "prefer_messages",
            "notes",
            "created_at",
        )
    }


@router.get("/{hospital_id}/director-feedback")
async def list_feedback(
    hospital_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    feedback_status: DirectorDeltaStatus = Query(DirectorDeltaStatus.ACTIVE),
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
):
    await _hospital(db, hospital_id, actor)
    rows = (
        (
            await db.execute(
                select(DirectorDelta)
                .where(
                    DirectorDelta.hospital_id == hospital_id,
                    DirectorDelta.status == feedback_status,
                )
                .order_by(DirectorDelta.created_at.desc(), DirectorDelta.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [_payload(row) for row in rows]


@router.post("/{hospital_id}/director-feedback")
async def add_feedback(
    hospital_id: uuid.UUID,
    body: FeedbackInput,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
):
    await _hospital(db, hospital_id, actor)
    if not any((body.avoid_messages, body.prefer_topics, body.prefer_messages)):
        raise HTTPException(422, "다음 글에 반영할 선호 또는 피할 표현을 입력해 주세요.")
    if body.report_id:
        report = await db.get(MonthlyReport, body.report_id)
        if report is None or report.hospital_id != hospital_id:
            raise HTTPException(404, "Report not found")
    payload = DirectorDeltaInput.model_validate(
        body.model_dump(exclude={"report_id", "conversation_reference"})
    )
    try:
        delta = await db.run_sync(create_director_delta, hospital_id, payload)
    except ActiveDirectorDeltaLimit as exc:
        raise HTTPException(409, str(exc)) from exc
    await write_audit_log(
        db,
        action="director_feedback_created",
        hospital_id=hospital_id,
        actor=actor.email,
        target_type="director_delta",
        target_id=delta.id,
        detail={
            "report_id": str(body.report_id) if body.report_id else None,
            "conversation_reference": body.conversation_reference,
            "preferences_only": True,
        },
    )
    await db.commit()
    await db.refresh(delta)
    return _payload(delta)


@router.post("/{hospital_id}/director-feedback/{delta_id}/retire")
async def retire_feedback(
    hospital_id: uuid.UUID,
    delta_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
):
    await _hospital(db, hospital_id, actor)
    try:
        delta = await db.run_sync(retire_director_delta, hospital_id, delta_id)
    except NoResultFound as exc:
        raise HTTPException(404, "Feedback not found") from exc
    await write_audit_log(
        db,
        action="director_feedback_retired",
        hospital_id=hospital_id,
        actor=actor.email,
        target_type="director_delta",
        target_id=delta.id,
        detail={"preferences_only": True},
    )
    await db.commit()
    await db.refresh(delta)
    return _payload(delta)
