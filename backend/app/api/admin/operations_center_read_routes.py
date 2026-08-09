"""Read-only routes for the unified operations center."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.operations_center_actions import (
    operations_error,
    require_operations_account,
    require_owner,
    scoped_run,
)
from app.api.admin.operations_center_incident_queries import load_incidents_queue
from app.api.admin.operations_center_queries import load_operations_queue
from app.api.admin.operations_center_query_common import OperationsFilters, normalize_filters
from app.api.admin.operations_center_serializers import run_summary
from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.operations import OperationRun
from app.schemas.operations import (
    IncidentDetailResponse,
    OperationsOverviewResponse,
    OperationsQueue,
    OperationsQueueResponse,
    OperationsQueueSummary,
    OperationsRunSummary,
)

router = APIRouter()
_PAGE_SIZE = 25
_OVERVIEW_SIZE = 5


@router.get("/overview", response_model=OperationsOverviewResponse)
async def get_operations_overview(
    owner: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    sla: str | None = None,
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_operations_account),
) -> OperationsOverviewResponse:
    """Return all queue counts plus the first five tasks in four fixed queries."""
    filters = normalize_filters(owner=owner, status=status, severity=severity, sla=sla)
    summaries: list[OperationsQueueSummary] = []
    items = []
    for queue in OperationsQueue:
        total, rows = await load_operations_queue(
            db, queue, filters, page=1, page_size=_OVERVIEW_SIZE, overview=True
        )
        summaries.append(
            OperationsQueueSummary(
                queue=queue,
                total=total,
                overdue=sum(row.sla_state == "OVERDUE" for row in rows),
            )
        )
        items.extend(rows)
    return OperationsOverviewResponse(queues=summaries, items=items)


@router.get("/queues/{queue}", response_model=OperationsQueueResponse)
async def get_operations_queue(
    queue: OperationsQueue,
    owner: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    sla: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_PAGE_SIZE, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_operations_account),
) -> OperationsQueueResponse:
    """Return one filtered queue using at most COUNT plus page SQL."""
    filters = normalize_filters(owner=owner, status=status, severity=severity, sla=sla)
    total, items = await load_operations_queue(
        db, queue, filters, page=page, page_size=page_size, overview=False
    )
    return OperationsQueueResponse(
        queue=queue, total=total, page=page, page_size=page_size, items=items
    )


async def _incident_detail(
    db: AsyncSession,
    incident_id: uuid.UUID,
    hospital_scope: uuid.UUID | None,
) -> IncidentDetailResponse:
    total, items = await load_incidents_queue(
        db,
        OperationsFilters(),
        page=1,
        page_size=1,
        overview=True,
        now=datetime.now(UTC),
        incident_id=incident_id,
        hospital_scope=hospital_scope,
    )
    if total == 0:
        raise operations_error(
            404, "INCIDENT_NOT_FOUND", "해당 범위의 운영 이슈를 찾을 수 없습니다."
        )
    item = items[0]
    run = await db.get(OperationRun, item.operation_run_id) if item.operation_run_id else None
    run_projection = run_summary(hospital_scope, run) if hospital_scope is not None else None
    return IncidentDetailResponse(incident=item, run=run_projection)


@router.get(
    "/hospitals/{hospital_id}/incidents/{incident_id}",
    response_model=IncidentDetailResponse,
)
async def get_incident_detail(
    hospital_id: uuid.UUID,
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_operations_account),
) -> IncidentDetailResponse:
    """Read one incident only through its owning hospital scope."""
    return await _incident_detail(db, incident_id, hospital_id)


@router.get("/incidents/{incident_id}", response_model=IncidentDetailResponse)
async def get_global_incident_detail(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_operations_account),
) -> IncidentDetailResponse:
    """Read a hospital-less system incident; owners only."""
    require_owner(actor)
    return await _incident_detail(db, incident_id, None)


@router.get("/hospitals/{hospital_id}/runs/{run_id}", response_model=OperationsRunSummary)
async def get_operation_run_detail(
    hospital_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_operations_account),
) -> OperationsRunSummary:
    """Read one safe run projection without stored payloads or task metadata."""
    run = await scoped_run(db, hospital_id, run_id)
    projection = run_summary(hospital_id, run)
    if projection is None:
        raise operations_error(404, "OPERATION_RUN_NOT_FOUND", "작업 기록을 찾을 수 없습니다.")
    return projection


__all__ = (
    "get_global_incident_detail",
    "get_incident_detail",
    "get_operation_run_detail",
    "get_operations_overview",
    "get_operations_queue",
    "router",
)
