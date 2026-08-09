"""Unified operations-center router facade."""

from fastapi import APIRouter

from app.api.admin.operations_center_actions import _TASK_POLICIES, _TaskPolicy
from app.api.admin.operations_center_incident_routes import (
    acknowledge_operations_incident,
    assign_operations_incident,
    recover_operations_incident,
)
from app.api.admin.operations_center_incident_routes import (
    router as incident_router,
)
from app.api.admin.operations_center_read_routes import (
    get_incident_detail,
    get_operation_run_detail,
    get_operations_overview,
    get_operations_queue,
)
from app.api.admin.operations_center_read_routes import (
    router as read_router,
)
from app.api.admin.operations_center_retry_routes import (
    retry_operations_notification,
    retry_operations_run,
)
from app.api.admin.operations_center_retry_routes import (
    router as retry_router,
)
from app.api.admin.operations_center_serializers import retry_action as _retry_action
from app.schemas.operations import (
    IncidentAssignRequest,
    NotificationRetryRequest,
    OperationRetryRequest,
    OperationsQueue,
    VersionedReasonRequest,
)

router = APIRouter(prefix="/admin/operations", tags=["Admin — Operations Center"])
router.include_router(read_router)
router.include_router(incident_router)
router.include_router(retry_router)

__all__ = (
    "IncidentAssignRequest",
    "NotificationRetryRequest",
    "OperationRetryRequest",
    "OperationsQueue",
    "VersionedReasonRequest",
    "_TASK_POLICIES",
    "_TaskPolicy",
    "_retry_action",
    "acknowledge_operations_incident",
    "assign_operations_incident",
    "get_incident_detail",
    "get_operation_run_detail",
    "get_operations_overview",
    "get_operations_queue",
    "recover_operations_incident",
    "retry_operations_notification",
    "retry_operations_run",
    "router",
)
