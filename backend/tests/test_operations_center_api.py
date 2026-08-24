"""Unified operations-center API contracts."""

import uuid
from datetime import UTC, datetime
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


def _incident(*, safe_error_code: str | None, safe_error_message: str | None):
    from app.models.operations import Incident

    now = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
    return Incident(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        operation_run_id=None,
        dedupe_key=f"test:{uuid.uuid4()}",
        incident_type="WEEKLY_SOV_MEASUREMENT_FAILED",
        state="OPEN",
        severity="HIGH",
        customer_impact="주간 AI 노출 측정이 지연됩니다.",
        source_type="WEEKLY_SOV",
        source_id="hospital:2026-W34",
        safe_error_code=safe_error_code,
        safe_error_message=safe_error_message,
        next_action="비용 한도를 확인하세요.",
        admin_path="/operations",
        first_seen_at=now,
        last_seen_at=now,
        occurrence_count=1,
        episode_seq=1,
        version=1,
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


def test_incident_projection_exposes_non_empty_stable_failure_cause() -> None:
    """FN-04: queue/detail rows retain a machine key and useful operator copy."""
    from app.api.admin.operations_center_serializers import serialize_incident_row

    incident = _incident(
        safe_error_code="WEEKLY_SOV_COST_GUARD_BLOCKED",
        safe_error_message="비용 한도를 초과해 측정이 차단되었습니다.",
    )

    row = serialize_incident_row(incident, None, None, None, None, incident.last_seen_at)

    assert row.cause_code == "COST_LIMIT_EXHAUSTED"
    assert row.cause_group_key == "COST_LIMIT_EXHAUSTED:sov"
    assert row.cost_guard_category == "sov"
    assert row.cause_message
    assert "한도" in row.cause_message
    assert row.safe_cause == row.cause_message


def test_incident_projection_never_emits_blank_cause_fields() -> None:
    """Legacy blank rows still receive a deterministic grouping key and cause."""
    from app.api.admin.operations_center_serializers import serialize_incident_row

    incident = _incident(safe_error_code="   ", safe_error_message="   ")
    incident.incident_type = ""
    incident.customer_impact = ""

    row = serialize_incident_row(incident, None, None, None, None, incident.last_seen_at)

    assert row.cause_code == "OPERATION_FAILED"
    assert row.cause_group_key == "OPERATION_FAILED"
    assert row.cause_message == "운영 작업이 완료되지 않은 원인을 확인해야 합니다."


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


def test_incident_recovery_filter_defaults_to_active_and_validates_values() -> None:
    from app.api.admin.operations_center_query_common import (
        IncidentRecoveryFilter,
        normalize_filters,
    )

    default = normalize_filters(owner=None, status=None, severity=None, sla=None)
    confirmed = normalize_filters(
        owner=None, status=None, severity=None, sla=None, recovery="confirmed"
    )

    assert default.recovery == IncidentRecoveryFilter.ACTIVE
    assert confirmed.recovery == IncidentRecoveryFilter.CONFIRMED
    with pytest.raises(HTTPException) as invalid:
        normalize_filters(
            owner=None, status=None, severity=None, sla=None, recovery="hidden"
        )
    assert invalid.value.detail["code"] == "INVALID_RECOVERY_FILTER"


def test_same_cause_incidents_collapse_with_distinct_hospital_count() -> None:
    from app.api.admin.operations_center_incident_queries import _group_incident_rows
    from app.models.hospital import Hospital

    first = _incident(
        safe_error_code="WEEKLY_SOV_COST_GUARD_BLOCKED",
        safe_error_message="측정 한도 소진",
    )
    second = _incident(safe_error_code="COST_BLOCKED", safe_error_message="측정 한도 소진")
    repeated_hospital = _incident(
        safe_error_code="COST_GUARD_LIMIT_REACHED", safe_error_message="측정 한도 소진 알림"
    )
    repeated_hospital.hospital_id = first.hospital_id
    hospitals = [
        Hospital(id=first.hospital_id, name="첫 병원", slug="first"),
        Hospital(id=second.hospital_id, name="둘째 병원", slug="second"),
        Hospital(id=first.hospital_id, name="첫 병원", slug="first"),
    ]
    grouped = _group_incident_rows(
        [
            (first, hospitals[0], None, None, None),
            (second, hospitals[1], None, None, None),
            (repeated_hospital, hospitals[2], None, None, None),
        ],
        first.last_seen_at,
    )

    assert len(grouped) == 1
    assert grouped[0].id == "cause:COST_LIMIT_EXHAUSTED:sov"
    assert grouped[0].same_type_count == 3
    assert grouped[0].affected_hospital_count == 2
