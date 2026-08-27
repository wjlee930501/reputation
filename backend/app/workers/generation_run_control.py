"""Durable control-plane records for content and image generation workers."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.operations import (
    JSONValue,
    OperationRun,
    OperationRunState,
)
from app.services.operation_run_payloads import (
    DispatchPayload,
    UnsafeDispatchPayload,
    build_request_payload,
    parse_stored_dispatch,
)

_SAFE_FAILURE_MESSAGE = "생성 작업이 완료되지 않았습니다. 운영 센터에서 원인을 확인해 주세요."


class GenerationItemState(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    DISCARDED = "DISCARDED"


class GenerationTaskRequest(Protocol):
    id: str | None
    headers: Mapping[str, str] | None
    operation_run_claim_version: int | None


class GenerationTask(Protocol):
    request: GenerationTaskRequest


_OPERATION_TASK_POLICIES = {
    "app.workers.tasks.trigger_v0_report": ("TRIGGER_V0_REPORT", "hospital", "reports"),
    "app.workers.tasks.build_aeo_site": ("REBUILD_SITE", "hospital", "default"),
    "app.workers.tasks.run_sov_for_hospital": ("RUN_SOV", "hospital", "sov"),
    "app.workers.tasks.regenerate_content_item": (
        "REGENERATE_CONTENT",
        "content_item",
        "content",
    ),
    "app.workers.tasks.generate_content_image": (
        "REGENERATE_CONTENT_IMAGE",
        "content_item",
        "content",
    ),
    "app.workers.tasks.generate_monthly_report_for_hospital": (
        "GENERATE_MONTHLY_REPORT",
        "hospital",
        "reports",
    ),
    "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_measurement": (
        "RECOVER_LEAD_MEASUREMENT",
        "lead_diagnosis",
        "leadgen",
    ),
    "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_report": (
        "RECOVER_LEAD_REPORT",
        "lead_diagnosis",
        "leadgen",
    ),
}
_OPERATION_RUN_REQUIRED_TASKS = frozenset(
    {
        "app.workers.tasks.generate_content_image",
        "app.workers.tasks.generate_monthly_report_for_hospital",
        "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_measurement",
        "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_report",
    }
)


@dataclass(frozen=True, slots=True)
class ExplicitRunContext:
    run_id: uuid.UUID
    worker_id: str
    version: int


def classify_generation_failure(error: BaseException) -> tuple[str, str]:
    """Map runtime failures to allowlisted operator-safe facts."""
    match error:
        case TimeoutError():
            code = "PROVIDER_TIMEOUT"
        case ConnectionError():
            code = "PROVIDER_UNAVAILABLE"
        case ValueError():
            code = "GENERATION_REJECTED"
        case _ if type(error).__name__ == "APITimeoutError":
            code = "PROVIDER_TIMEOUT"
        case _:
            code = "GENERATION_FAILED"
    return code, _SAFE_FAILURE_MESSAGE


def explicit_run_context(task: GenerationTask) -> ExplicitRunContext | None:
    headers = task.request.headers
    run_id = headers.get("operation_run_id") if isinstance(headers, Mapping) else None
    worker_id = task.request.id
    version = getattr(task.request, "operation_run_claim_version", None)
    if (
        not isinstance(run_id, str)
        or not isinstance(worker_id, str)
        or not isinstance(version, int)
    ):
        return None
    try:
        return ExplicitRunContext(uuid.UUID(run_id), worker_id, version)
    except ValueError:
        return None


def explicit_run_matches(
    db: Session,
    task: GenerationTask,
    item_id: uuid.UUID | str,
    hospital_id: uuid.UUID | str,
) -> bool:
    """Prove that the claimed Admin run authorizes this exact tenant target."""
    context = explicit_run_context(task)
    if context is None:
        return False
    run = db.get(OperationRun, context.run_id)
    if run is None:
        return False
    payload = run.request_payload if isinstance(run.request_payload, Mapping) else {}
    state = getattr(run.state, "value", run.state)
    return (
        run.operation_type == "REGENERATE_CONTENT"
        and state == OperationRunState.RUNNING.value
        and run.task_id == context.worker_id
        and run.lease_owner == context.worker_id
        and run.version == context.version
        and str(run.hospital_id) == str(hospital_id)
        and payload.get("source_type") == "content_item"
        and payload.get("source_id") == str(item_id)
    )


def operation_run_dispatch_authorized(
    db: Session,
    task: GenerationTask,
    task_name: str,
    task_args: Sequence[JSONValue],
) -> bool:
    """Bind an Admin dispatch to its claimed run, tenant, target, queue, and arguments."""
    context = explicit_run_context(task)
    policy = _OPERATION_TASK_POLICIES.get(task_name)
    if context is None or policy is None:
        return False
    run = db.get(OperationRun, context.run_id)
    if run is None:
        return False
    operation_type, target_type, queue = policy
    try:
        dispatch = parse_stored_dispatch(run.request_payload.get("_dispatch"))
    except (AttributeError, UnsafeDispatchPayload):
        return False
    state = getattr(run.state, "value", run.state)
    return (
        run.operation_type == operation_type
        and state == OperationRunState.RUNNING.value
        and run.task_id == context.worker_id
        and run.lease_owner == context.worker_id
        and run.version == context.version
        and dispatch.target_type == target_type
        and dispatch.target_id
        == str(run.hospital_id if target_type == "hospital" else task_args[0])
        and dispatch.queue == queue
        and dispatch.task_args == tuple(task_args)
    )


def operation_run_required(task_name: str) -> bool:
    return task_name in _OPERATION_RUN_REQUIRED_TASKS


def finish_explicit_run(
    db: Session,
    task: GenerationTask,
    item_id: uuid.UUID,
    state: OperationRunState,
    *,
    safe_error_code: str | None = None,
    safe_error_message: str | None = None,
) -> uuid.UUID | None:
    """Terminalize an Admin-dispatched run; the generic Celery signal then no-ops."""
    context = explicit_run_context(task)
    if context is None:
        return None
    success = int(state == OperationRunState.SUCCEEDED)
    failure = int(state in (OperationRunState.FAILED, OperationRunState.PARTIAL))
    item_result: dict[str, JSONValue] = {
        "state": state.value,
        "attempt_id": f"{context.run_id}:{item_id}:{context.version}",
    }
    if safe_error_code is not None:
        item_result.update(
            {
                "safe_error_code": safe_error_code,
                "safe_error_message": safe_error_message,
                "next_retry_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
            }
        )
    result = db.execute(
        update(OperationRun)
        .where(
            OperationRun.id == context.run_id,
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.lease_owner == context.worker_id,
            OperationRun.version == context.version,
        )
        .values(
            state=state,
            completed_at=datetime.now(UTC),
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            total_count=1,
            success_count=success,
            failure_count=failure,
            skipped_count=int(state == OperationRunState.CANCELLED),
            result_summary={"items": {str(item_id): item_result}},
            safe_error_code=safe_error_code,
            safe_error_message=safe_error_message,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun.id)
    ).scalar_one_or_none()
    db.commit()
    return result


def create_item_run(
    db: Session,
    *,
    parent_run_id: uuid.UUID,
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    operation_type: str,
    state: OperationRunState,
    result: JSONValue,
    safe_error_code: str | None = None,
    safe_error_message: str | None = None,
    attempt_kind: str = "final",
) -> OperationRun:
    success = int(state == OperationRunState.SUCCEEDED)
    failure = int(state in (OperationRunState.FAILED, OperationRunState.PARTIAL))
    child = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type=operation_type,
        state=state,
        idempotency_key=f"{parent_run_id}:{item_id}:{operation_type}:{attempt_kind}",
        parent_run_id=parent_run_id,
        request_payload=build_request_payload(
            DispatchPayload("content_item", str(item_id), "content", (str(item_id),))
        ),
        attempt_count=1,
        total_count=1,
        success_count=success,
        failure_count=failure,
        skipped_count=int(state == OperationRunState.CANCELLED),
        result_summary={"items": {str(item_id): result}},
        safe_error_code=safe_error_code,
        safe_error_message=safe_error_message,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        version=1,
    )
    db.add(child)
    db.commit()
    return child
