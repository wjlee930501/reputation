"""Unified operations-center API contracts."""

import uuid
from datetime import UTC, datetime, timedelta
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


def test_incident_rows_mark_automatic_retries_as_context_not_operator_work() -> None:
    """RETRYING rows stay in the response but stop counting as work waiting on a person."""
    from app.api.admin.operations_center_serializers import serialize_incident_row

    # Given: the same incident while automatic recovery owns it and after it stops
    retrying = _incident(safe_error_code="PROVIDER_TIMEOUT", safe_error_message="일시 지연")
    retrying.state = "RETRYING"
    waiting = _incident(safe_error_code="PROVIDER_TIMEOUT", safe_error_message="일시 지연")

    # When
    retrying_row = serialize_incident_row(
        retrying, None, None, None, None, retrying.last_seen_at
    )
    waiting_row = serialize_incident_row(waiting, None, None, None, None, waiting.last_seen_at)

    # Then: the row survives for the FE to collapse, with the flag telling it apart
    assert retrying_row.status == "RETRYING"
    assert retrying_row.requires_operator_action is False
    assert waiting_row.requires_operator_action is True


def test_retrying_incident_becomes_operator_work_only_after_its_deadline() -> None:
    from app.api.admin.operations_center_serializers import serialize_incident_row

    now = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
    incident = _incident(safe_error_code="PROVIDER_TIMEOUT", safe_error_message="일시 지연")
    incident.state = "RETRYING"
    incident.sla_due_at = now + timedelta(minutes=10)

    before_deadline = serialize_incident_row(incident, None, None, None, None, now)
    incident.sla_due_at = now - timedelta(seconds=1)
    after_deadline = serialize_incident_row(incident, None, None, None, None, now)

    assert before_deadline.requires_operator_action is False
    assert after_deadline.requires_operator_action is True


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
        "RECERTIFY_PUBLISHED_IMAGE": "content",
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


def _recertify_run(
    *,
    code: str | None,
    title: str = "치질 증상",
    item_id=None,
    hospital_id=None,
    state: str = "FAILED",
):
    from app.models.content import ContentType
    from app.models.operations import OperationRun
    from app.services import published_image_recertification as recertification
    from app.services.image_engine import image_subject_hash

    item_id = item_id or uuid.uuid4()
    now = datetime.now(UTC)
    terminal = state not in ("REQUESTED", "QUEUED", "RUNNING")
    return OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id or uuid.uuid4(),
        operation_type=recertification.RECERTIFY_OPERATION,
        state=state,
        safe_error_code=code,
        requested_at=now,
        completed_at=now if terminal else None,
        request_payload=recertification.request_payload(
            item_id,
            subject_hash=image_subject_hash(ContentType.DISEASE, title),
            title=title,
            revision=3,
        ),
    )


class _RunLookup:
    """재시도 예산 검사가 읽는 실행 이력만 돌려주는 최소 더블."""

    def __init__(self, runs):
        self._runs = runs

    async def execute(self, _statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self._runs))


@pytest.mark.asyncio
async def test_operator_retry_is_refused_for_a_recertification_a_human_must_decide() -> None:
    """거절로 끝난 재인증은 다시 눌러도 같은 답을 유료로 살 뿐이다 (H-01)."""
    from app.api.admin.operations_center_actions import require_retry_within_budget
    from app.api.admin.operations_center_serializers import retry_action
    from app.services import published_image_recertification as recertification

    run = _recertify_run(code=recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED)

    assert retry_action(run.hospital_id, run) is None
    with pytest.raises(HTTPException) as refused:
        await require_retry_within_budget(_RunLookup([run]), run)
    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "OPERATION_NOT_RETRYABLE"


@pytest.mark.asyncio
async def test_operator_retry_is_refused_while_the_same_subject_is_in_flight() -> None:
    """진행 중인 실행 위에 사람이 한 번 더 얹으면 같은 답을 두 번 산다 (H-01)."""
    from app.api.admin.operations_center_actions import require_retry_within_budget

    item_id, hospital_id = uuid.uuid4(), uuid.uuid4()
    failed = _recertify_run(
        code="PROVIDER_UNAVAILABLE", item_id=item_id, hospital_id=hospital_id
    )
    running = _recertify_run(
        code=None, item_id=item_id, hospital_id=hospital_id, state="RUNNING"
    )

    with pytest.raises(HTTPException) as refused:
        await require_retry_within_budget(_RunLookup([failed, running]), failed)
    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "OPERATION_NOT_RETRYABLE"


@pytest.mark.asyncio
async def test_operator_retry_follows_the_same_attempt_budget_as_the_sweep() -> None:
    """운영자 재시도도 (글, subject) 예산 안에서만 허용된다."""
    from app.api.admin.operations_center_actions import require_retry_within_budget
    from app.api.admin.operations_center_serializers import retry_action
    from app.services import published_image_recertification as recertification

    item_id, hospital_id = uuid.uuid4(), uuid.uuid4()
    failed = _recertify_run(
        code="PROVIDER_UNAVAILABLE", item_id=item_id, hospital_id=hospital_id
    )
    assert retry_action(hospital_id, failed) is not None
    await require_retry_within_budget(_RunLookup([failed]), failed)

    spent = [
        _recertify_run(
            code="PROVIDER_UNAVAILABLE", item_id=item_id, hospital_id=hospital_id
        )
        for _ in range(recertification.ATTEMPT_BUDGET)
    ]
    with pytest.raises(HTTPException) as exhausted:
        await require_retry_within_budget(_RunLookup(spent), spent[0])
    assert exhausted.value.status_code == 409
    assert recertification.OPERATOR_ACTION in exhausted.value.detail["message"]

    # 제목이 바뀌면 새 subject다. 이전 제목의 소진이 새 제목을 막지 않는다.
    fresh = _recertify_run(
        code="PROVIDER_UNAVAILABLE",
        title="다른 제목",
        item_id=item_id,
        hospital_id=hospital_id,
    )
    await require_retry_within_budget(_RunLookup([*spent, fresh]), fresh)


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


def test_operations_filters_preserve_hospital_scope() -> None:
    from app.api.admin.operations_center_query_common import normalize_filters

    hospital_id = uuid.uuid4()
    filters = normalize_filters(
        hospital_id=hospital_id,
        owner=None,
        status=None,
        severity=None,
        sla=None,
    )

    assert filters.hospital_id == hospital_id


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


def _operator(role: str, *, user_id: uuid.UUID | None = None):
    from app.models.admin_user import AdminUser

    return AdminUser(
        id=user_id or uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.test",
        name="운영자",
        role=role,
        password_hash="not-a-real-hash",
        is_active=True,
    )


def _failed_run(hospital_id: uuid.UUID):
    from app.models.operations import OperationRun

    return OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="REGENERATE_CONTENT",
        state="FAILED",
        request_payload={},
    )


def test_incident_row_actions_follow_the_authorization_the_routes_enforce() -> None:
    """행이 싣는 행동의 활성 여부 = `require_owner`·`authorize_run_retry`의 답."""
    from app.api.admin.operations_center_serializers import serialize_incident_row
    from app.models.admin_user import ROLE_OPERATOR, ROLE_OWNER

    incident = _incident(safe_error_code="PROVIDER_TIMEOUT", safe_error_message="지연")
    run = _failed_run(incident.hospital_id)
    assignee = _operator(ROLE_OPERATOR)
    incident.owner_id = assignee.id

    owner_row = serialize_incident_row(
        incident, None, None, run, None, incident.last_seen_at, actor=_operator(ROLE_OWNER)
    )
    assignee_row = serialize_incident_row(
        incident, None, None, run, None, incident.last_seen_at, actor=assignee
    )
    stranger_row = serialize_incident_row(
        incident, None, None, run, None, incident.last_seen_at, actor=_operator(ROLE_OPERATOR)
    )

    assert owner_row.assign is not None
    assert owner_row.assign.kind == "ASSIGN_INCIDENT"
    assert owner_row.assign.requires_version is True
    assert owner_row.assign.reason_required is True
    assert owner_row.assign.enabled is True
    # 담당 지정은 OWNER 전용이지만, 재시도는 담당자에게도 열려 있다.
    assert assignee_row.assign is not None and assignee_row.assign.enabled is False
    assert assignee_row.retry is not None and assignee_row.retry.enabled is True
    assert stranger_row.retry is not None and stranger_row.retry.enabled is False


def test_incident_row_without_a_known_actor_keeps_the_previous_contract() -> None:
    """요청자를 모르는 호출(배치·기존 경로)은 인가에 달린 행동을 만들지 않는다."""
    from app.api.admin.operations_center_serializers import serialize_incident_row

    incident = _incident(safe_error_code="PROVIDER_TIMEOUT", safe_error_message="지연")
    run = _failed_run(incident.hospital_id)

    row = serialize_incident_row(incident, None, None, run, None, incident.last_seen_at)

    assert row.assign is None
    assert row.resolve is None
    assert row.retry is not None and row.retry.enabled is True


def test_recovery_confirmation_waits_for_the_linked_run_to_succeed() -> None:
    """복구 확인은 연결 작업 성공이 관측돼야 서버가 받는다 — 버튼도 그때 켜진다."""
    from app.api.admin.operations_center_serializers import serialize_incident_row
    from app.models.admin_user import ROLE_OWNER

    incident = _incident(safe_error_code="PROVIDER_TIMEOUT", safe_error_message="지연")
    incident.state = "RETRYING"
    run = _failed_run(incident.hospital_id)
    owner = _operator(ROLE_OWNER)

    while_failing = serialize_incident_row(
        incident, None, None, run, None, incident.last_seen_at, actor=owner
    )
    run.state = "SUCCEEDED"
    after_success = serialize_incident_row(
        incident, None, None, run, None, incident.last_seen_at, actor=owner
    )
    incident.state = "RECOVERED"
    recovered = serialize_incident_row(
        incident, None, None, run, None, incident.last_seen_at, actor=owner
    )

    assert while_failing.resolve is not None
    assert while_failing.resolve.kind == "RECOVER_INCIDENT"
    assert while_failing.resolve.enabled is False
    assert after_success.resolve is not None and after_success.resolve.enabled is True
    assert after_success.resolve.path.endswith("/recover")
    assert recovered.resolve is not None
    assert recovered.resolve.kind == "ACK_INCIDENT"
    assert recovered.resolve.enabled is True
