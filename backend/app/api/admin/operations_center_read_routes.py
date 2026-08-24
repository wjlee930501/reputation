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
from app.api.admin.operations_center_query_common import (
    IncidentRecoveryFilter,
    OperationsFilters,
    normalize_filters,
)
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
    recovery: str | None = None,
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_operations_account),
) -> OperationsOverviewResponse:
    """Return all queue counts plus the first five tasks in four fixed queries."""
    filters = normalize_filters(
        owner=owner, status=status, severity=severity, sla=sla, recovery=recovery
    )
    summaries: list[OperationsQueueSummary] = []
    items = []
    for queue in OperationsQueue:
        total, rows = await load_operations_queue(
            db, queue, filters, page=1, page_size=_OVERVIEW_SIZE, overview=True
        )
        # 기한 초과 건수는 여기서 세지 않는다. overview는 큐마다 앞 5건만 읽으므로,
        # 그 5건에서 센 숫자는 실제 총계가 아니라 표본이다. 6번째 이후의 초과 건은
        # 언제나 0으로 보고됐고, 화면이 그 숫자를 "N건 초과"로 쓰면 밀린 일을 숨긴다.
        # 총계를 정직하게 세려면 큐마다 조회가 하나 더 필요한데, overview는 12초마다
        # 폴링되며 쿼리 수 상한(5)이 가드로 고정돼 있다. 그래서 틀린 숫자를 내보내는
        # 대신 내보내지 않는다.
        summaries.append(OperationsQueueSummary(queue=queue, total=total))
        items.extend(rows)
    return OperationsOverviewResponse(queues=summaries, items=items)


@router.get("/queues/{queue}", response_model=OperationsQueueResponse)
async def get_operations_queue(
    queue: OperationsQueue,
    owner: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    sla: str | None = None,
    recovery: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_PAGE_SIZE, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_operations_account),
) -> OperationsQueueResponse:
    """Return one filtered queue using at most COUNT plus page SQL."""
    filters = normalize_filters(
        owner=owner, status=status, severity=severity, sla=sla, recovery=recovery
    )
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
        OperationsFilters(recovery=IncidentRecoveryFilter.ALL),
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
