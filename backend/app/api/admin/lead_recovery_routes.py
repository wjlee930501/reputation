"""HTTP boundary for terminal lead-diagnosis recovery."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.accounts import require_active_account
from app.api.admin.lead_recovery import RecoveryAxis, _dispatch_recovery
from app.core.database import get_db
from app.models.admin_user import AdminUser

router = APIRouter()
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


class LeadRecoveryRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str = Field(min_length=3, max_length=200)


@router.post("/{lead_id}/diagnoses/{diagnosis_id}/remeasure")
async def remeasure_lead_diagnosis(
    lead_id: uuid.UUID,
    diagnosis_id: uuid.UUID,
    body: LeadRecoveryRequest,
    idempotency_key: IdempotencyKey,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
) -> dict[str, str | bool]:
    return await _dispatch_recovery(
        db,
        lead_id=lead_id,
        diagnosis_id=diagnosis_id,
        axis=RecoveryAxis.MEASUREMENT,
        reason=body.reason,
        idempotency_key=idempotency_key,
        actor=actor,
    )


@router.post("/{lead_id}/diagnoses/{diagnosis_id}/rebuild-report")
async def rebuild_lead_diagnosis_report(
    lead_id: uuid.UUID,
    diagnosis_id: uuid.UUID,
    body: LeadRecoveryRequest,
    idempotency_key: IdempotencyKey,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
) -> dict[str, str | bool]:
    return await _dispatch_recovery(
        db,
        lead_id=lead_id,
        diagnosis_id=diagnosis_id,
        axis=RecoveryAxis.REPORT,
        reason=body.reason,
        idempotency_key=idempotency_key,
        actor=actor,
    )
