"""Durable control-plane records for content and image generation workers."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
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
from app.services.operation_run_payloads import DispatchPayload, build_request_payload

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
