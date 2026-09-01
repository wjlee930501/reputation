"""Unified operations-center router facade."""

from fastapi import APIRouter

from app.api.admin.operations_center_actions import _TASK_POLICIES
from app.api.admin.operations_center_incident_routes import (
    router as incident_router,
)
from app.api.admin.operations_center_read_routes import (
    router as read_router,
)
from app.api.admin.operations_center_retry_routes import (
    router as retry_router,
)
from app.api.admin.operations_center_serializers import retry_action as _retry_action

router = APIRouter(prefix="/admin/operations", tags=["Admin — Operations Center"])
router.include_router(read_router)
router.include_router(incident_router)
router.include_router(retry_router)

# `_TASK_POLICIES`/`_retry_action` stay importable from this facade module —
# tests import them from here (`test_operations_center_api.py`), not from the
# submodules they're actually defined in.
__all__ = ("router", "_TASK_POLICIES", "_retry_action")
