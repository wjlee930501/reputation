"""Unified operations-center API contracts."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from slowapi import Limiter

from app.core.rate_limit import get_request_ip
from app.main import app
from app.schemas.operations import (
    OperationsQueueRow,
    OperationsRunSummary,
    OperationsSlackState,
)


def test_overview_requires_an_active_operator_account() -> None:
    """Given an authenticated admin key without an active actor, return 403."""

    # Given / When
    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/admin/operations/overview",
                headers={"X-Admin-Key": "test-admin-key"},
            )
    finally:
        app.state.limiter = previous_limiter

    # Then
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACTIVE_ACCOUNT_REQUIRED"


def test_operations_response_schemas_cannot_serialize_internal_secrets() -> None:
    """Given the public response types, sensitive storage fields are absent by construction."""

    # Given
    prohibited = {
        "request_payload",
        "result_summary",
        "payload",
        "provider_response",
        "task_id",
        "lease_owner",
        "lease_expires_at",
    }

    # When
    exposed = set().union(
        OperationsQueueRow.model_fields,
        OperationsRunSummary.model_fields,
        OperationsSlackState.model_fields,
    )

    # Then
    assert prohibited.isdisjoint(exposed)


def test_operation_actions_distinguish_browser_navigation_from_bff_mutations() -> None:
    """Mutation paths target the Admin BFF while links remain browser navigation paths."""

    from app.api.admin.operations_center import _retry_action
    from app.models.operations import OperationRun

    hospital_id = uuid.uuid4()
    run = OperationRun(
        operation_type="TRIGGER_V0_REPORT",
        state="FAILED",
        request_payload={},
    )

    action = _retry_action(hospital_id, run)

    assert action is not None
    assert action.path.startswith("/api/admin/operations/")
    assert action.reason_required is True
    assert action.requires_idempotency_key is True


def test_retry_task_policies_match_celery_routes_and_consumed_queues() -> None:
    """Every retry must target the queue used by the authoritative Celery route."""

    from app.api.admin.operations_center import _TASK_POLICIES
    from app.core.celery_app import celery_app

    consumed_queues = {"default", "content", "sov", "reports"}
    routes = celery_app.conf.task_routes
    expected_queues = {
        "TRIGGER_V0_REPORT": "reports",
        "RUN_SOV": "sov",
        "REBUILD_SITE": "default",
        "GENERATE_MONTHLY_REPORT": "reports",
        "REGENERATE_CONTENT": "content",
        "REGENERATE_CONTENT_IMAGE": "content",
    }

    for operation_type, policy in _TASK_POLICIES.items():
        assert policy.queue == expected_queues[operation_type]
        assert policy.queue in consumed_queues
        route = routes.get(policy.task.name)
        if route is not None:
            assert route["queue"] == policy.queue


@pytest.mark.asyncio
async def test_retry_policy_allows_monthly_rebuild_true_payload_only() -> None:
    """Manual retry must support rebuild runs without opening arbitrary task args."""

    from app.api.admin.operations_center_actions import retry_policy
    from app.models.operations import OperationRun

    hospital_id = uuid.uuid4()

    def run_with_args(args: list[str | int | bool]) -> OperationRun:
        return OperationRun(
            hospital_id=hospital_id,
            operation_type="GENERATE_MONTHLY_REPORT",
            state="FAILED",
            request_payload={
                "_dispatch": {
                    "target_type": "hospital",
                    "target_id": str(hospital_id),
                    "queue": "reports",
                    "task_args": args,
                }
            },
        )

    db = SimpleNamespace()

    assert (await retry_policy(db, run_with_args([str(hospital_id), 2026, 7]))).queue == "reports"
    assert (
        await retry_policy(db, run_with_args([str(hospital_id), 2026, 7, True]))
    ).queue == "reports"

    for invalid_args in (
        [str(hospital_id), 2026, 7, False],
        [str(hospital_id), 2026, 7, True, 1],
    ):
        with pytest.raises(HTTPException) as blocked:
            await retry_policy(db, run_with_args(invalid_args))
        assert blocked.value.status_code == 422
        assert blocked.value.detail["code"] == "UNSAFE_STORED_DISPATCH"


def test_invalid_sla_filter_returns_a_typed_422() -> None:
    from app.api.admin.operations_center_query_common import normalize_filters

    with pytest.raises(HTTPException) as invalid:
        normalize_filters(
            owner=None,
            status=None,
            severity=None,
            sla="SOMEDAY",
        )

    assert invalid.value.status_code == 422
    assert invalid.value.detail["code"] == "INVALID_SLA_FILTER"
    message = invalid.value.detail["message"]
    assert "처리 기한" in message
    assert "SLA" not in message
    assert all(raw not in message for raw in ("OVERDUE", "DUE", "NONE"))
