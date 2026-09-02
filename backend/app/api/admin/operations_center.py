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

# 아래 이름들은 이 파사드에서 계속 import 가능해야 한다. 라우트 핸들러·요청 스키마를
# 직접 호출하는 테스트(`tests/integration/test_attention_queue.py`,
# `tests/test_operations_center_api.py`)가 하위 모듈이 아니라 이 모듈을 통해 접근한다.
# 그중 test_attention_queue.py는 Postgres가 있을 때만 도는 통합 테스트라, 참조를
# 정적 분석만으로 세면 "미사용"으로 잘못 보인다. 삭제 전 반드시 통합 테스트까지 실행할 것.
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
