"""Incident ownership and lifecycle mutation routes."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.operations_center_actions import (
    operations_error,
    raise_incident_conflict,
    require_assignee_or_owner,
    require_operations_account,
    require_owner,
    scoped_incident,
)
from app.api.admin.operations_center_read_routes import _incident_detail
from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.operations import Incident, OperationRun, OperationRunState
from app.schemas.operations import (
    IncidentAssignRequest,
    IncidentDetailResponse,
    VersionedReasonRequest,
)
from app.services.incidents import acknowledge_incident, assign_incident, mark_recovered

router = APIRouter()


async def _load_incident(
    db: AsyncSession,
    hospital_id: uuid.UUID | None,
    incident_id: uuid.UUID,
) -> Incident:
    if hospital_id is not None:
        return await scoped_incident(db, hospital_id, incident_id)
    incident = await db.scalar(
        select(Incident).where(Incident.id == incident_id, Incident.hospital_id.is_(None))
    )
    if incident is None:
        raise operations_error(
            404, "INCIDENT_NOT_FOUND", "전체 시스템 운영 이슈를 찾을 수 없습니다."
        )
    return incident


async def _assign(
    db: AsyncSession,
    actor: AdminUser,
    hospital_id: uuid.UUID | None,
    incident_id: uuid.UUID,
    body: IncidentAssignRequest,
) -> IncidentDetailResponse:
    require_owner(actor)
    await _load_incident(db, hospital_id, incident_id)
    if body.owner_id is not None:
        owner = await db.scalar(
            select(AdminUser).where(AdminUser.id == body.owner_id, AdminUser.is_active.is_(True))
        )
        if owner is None:
            raise operations_error(
                422, "INVALID_OWNER", "활성 운영자만 담당자로 지정할 수 있습니다."
            )
    result = await assign_incident(
        db,
        incident_id,
        expected_version=body.expected_version,
        owner_id=body.owner_id,
        sla_due_at=body.sla_due_at,
        actor=actor.email,
        reason=body.reason,
    )
    if not isinstance(result, Incident):
        raise_incident_conflict(result, hospital_id)
    await db.commit()
    return await _incident_detail(db, incident_id, hospital_id)


async def _acknowledge(
    db: AsyncSession,
    actor: AdminUser,
    hospital_id: uuid.UUID | None,
    incident_id: uuid.UUID,
    body: VersionedReasonRequest,
) -> IncidentDetailResponse:
    incident = await _load_incident(db, hospital_id, incident_id)
    if hospital_id is None:
        require_owner(actor)
    else:
        require_assignee_or_owner(actor, incident)
    result = await acknowledge_incident(
        db,
        incident_id,
        expected_version=body.expected_version,
        acknowledged_by_id=actor.id,
        actor=actor.email,
        reason=body.reason,
    )
    if not isinstance(result, Incident):
        raise_incident_conflict(result, hospital_id)
    await db.commit()
    return await _incident_detail(db, incident_id, hospital_id)


async def _recover(
    db: AsyncSession,
    actor: AdminUser,
    hospital_id: uuid.UUID | None,
    incident_id: uuid.UUID,
    body: VersionedReasonRequest,
) -> IncidentDetailResponse:
    incident = await _load_incident(db, hospital_id, incident_id)
    if hospital_id is None:
        require_owner(actor)
    else:
        require_assignee_or_owner(actor, incident)
    run_scope = (
        OperationRun.hospital_id.is_(None)
        if hospital_id is None
        else OperationRun.hospital_id == hospital_id
    )
    linked_success = (
        incident.operation_run_id is not None
        and await db.scalar(
            select(func.count(OperationRun.id)).where(
                OperationRun.id == incident.operation_run_id,
                run_scope,
                OperationRun.state == OperationRunState.SUCCEEDED,
            )
        )
        == 1
    )
    if not linked_success:
        path = (
            f"/api/admin/operations/incidents/{incident_id}"
            if hospital_id is None
            else f"/api/admin/operations/hospitals/{hospital_id}/incidents/{incident_id}"
        )
        raise operations_error(
            409,
            "INCIDENT_RECOVERY_NOT_OBSERVED",
            "연결된 작업의 성공이 확인되지 않아 복구 완료로 바꿀 수 없습니다.",
            current_version=incident.version,
            current_state=incident.state,
            refetch_path=path,
        )
    result = await mark_recovered(
        db,
        incident_id,
        expected_version=body.expected_version,
        observed_success=True,
        actor=actor.email,
        reason=body.reason,
    )
    if not isinstance(result, Incident):
        raise_incident_conflict(result, hospital_id)
    await db.commit()
    return await _incident_detail(db, incident_id, hospital_id)


@router.post(
    "/hospitals/{hospital_id}/incidents/{incident_id}/assign",
    response_model=IncidentDetailResponse,
)
async def assign_operations_incident(
    hospital_id: uuid.UUID,
    incident_id: uuid.UUID,
    body: IncidentAssignRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_operations_account),
) -> IncidentDetailResponse:
    return await _assign(db, actor, hospital_id, incident_id, body)


@router.post(
    "/hospitals/{hospital_id}/incidents/{incident_id}/ack",
    response_model=IncidentDetailResponse,
)
async def acknowledge_operations_incident(
    hospital_id: uuid.UUID,
    incident_id: uuid.UUID,
    body: VersionedReasonRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_operations_account),
) -> IncidentDetailResponse:
    return await _acknowledge(db, actor, hospital_id, incident_id, body)


@router.post(
    "/hospitals/{hospital_id}/incidents/{incident_id}/recover",
    response_model=IncidentDetailResponse,
)
async def recover_operations_incident(
    hospital_id: uuid.UUID,
    incident_id: uuid.UUID,
    body: VersionedReasonRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_operations_account),
) -> IncidentDetailResponse:
    return await _recover(db, actor, hospital_id, incident_id, body)


@router.post("/incidents/{incident_id}/assign", response_model=IncidentDetailResponse)
async def assign_global_incident(
    incident_id: uuid.UUID,
    body: IncidentAssignRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_operations_account),
) -> IncidentDetailResponse:
    return await _assign(db, actor, None, incident_id, body)


@router.post("/incidents/{incident_id}/ack", response_model=IncidentDetailResponse)
async def acknowledge_global_incident(
    incident_id: uuid.UUID,
    body: VersionedReasonRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_operations_account),
) -> IncidentDetailResponse:
    return await _acknowledge(db, actor, None, incident_id, body)


@router.post("/incidents/{incident_id}/recover", response_model=IncidentDetailResponse)
async def recover_global_incident(
    incident_id: uuid.UUID,
    body: VersionedReasonRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_operations_account),
) -> IncidentDetailResponse:
    return await _recover(db, actor, None, incident_id, body)


__all__ = (
    "acknowledge_operations_incident",
    "assign_operations_incident",
    "recover_operations_incident",
    "router",
)
