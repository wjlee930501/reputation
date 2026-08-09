"""Durable command dispatch and compare-and-swap OperationRun transitions."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from kombu.exceptions import OperationalError as BrokerOperationalError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import IncidentSeverity, JSONValue, OperationRun, OperationRunState
from app.services import operation_run_keys as run_keys
from app.services import operation_run_payloads as run_payloads
from app.services import operation_run_transitions as transitions
from app.services.audit_log import write_audit_log
from app.services.incidents import (
    IncidentFingerprint,
    IncidentOpenRequest,
    open_or_touch_incident,
)

Heartbeat = transitions.Heartbeat
LeaseClaim = transitions.LeaseClaim
TerminalResult = transitions.TerminalResult
TerminalTransition = transitions.TerminalTransition
claim_operation_run = transitions.claim_operation_run
finish_operation_run = transitions.finish_operation_run
heartbeat_operation_run = transitions.heartbeat_operation_run
UnsafeDispatchArgument = run_payloads.UnsafeDispatchPayload

_BROKER_ERROR_MESSAGE = "작업 큐 연결에 실패했습니다. 잠시 후 다시 시도해 주세요."


class TaskResult(Protocol):
    id: str | None


class DispatchTask(Protocol):
    def apply_async(
        self,
        *,
        args: list[JSONValue],
        queue: str,
        headers: dict[str, str],
        task_id: str,
    ) -> TaskResult: ...


@dataclass(frozen=True, slots=True)
class OperationCommand:
    operation_type: str
    hospital_id: uuid.UUID | None
    requested_by_id: uuid.UUID | None
    idempotency_key: str | None
    audit_actor: str
    target_type: str
    target_id: str
    queue: str
    task_args: tuple[JSONValue, ...]
    parent_run_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class OperationDispatch:
    run: OperationRun
    replayed: bool


@dataclass(frozen=True, slots=True)
class RetryCommand:
    run_id: uuid.UUID
    requested_by_id: uuid.UUID | None
    audit_actor: str
    request_key: str


@dataclass(frozen=True, slots=True)
class OperationQueueUnavailable(Exception):
    run_id: uuid.UUID

    def __str__(self) -> str:
        return _BROKER_ERROR_MESSAGE


async def dispatch_operation(
    db: AsyncSession, command: OperationCommand, task: DispatchTask
) -> OperationDispatch:
    existing = await _find_idempotent_run(db, command)
    if existing is not None:
        return OperationDispatch(run=existing, replayed=True)

    broker_task_id = str(uuid.uuid4())
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=command.hospital_id,
        operation_type=command.operation_type,
        state=OperationRunState.REQUESTED,
        idempotency_key=run_keys.normalize_operation_key(command.idempotency_key),
        requested_by_id=command.requested_by_id,
        parent_run_id=command.parent_run_id,
        task_id=broker_task_id,
        request_payload=run_payloads.build_request_payload(
            run_payloads.DispatchPayload(
                command.target_type,
                command.target_id,
                command.queue,
                command.task_args,
            )
        ),
        attempt_count=0,
        total_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        version=1,
    )
    db.add(run)
    await _write_run_audit(db, command, run, "requested", queued=False)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        replay = await _find_idempotent_run(db, command)
        if replay is None:
            raise
        return OperationDispatch(run=replay, replayed=True)

    try:
        task.apply_async(
            args=list(command.task_args),
            queue=command.queue,
            headers={"operation_run_id": str(run.id)},
            task_id=broker_task_id,
        )
    except (BrokerOperationalError, OSError) as exc:
        run.state = OperationRunState.FAILED
        run.completed_at = datetime.now(UTC)
        run.safe_error_code = "BROKER_UNAVAILABLE"
        run.safe_error_message = _BROKER_ERROR_MESSAGE
        run.version += 1
        await _open_queue_incident(db, run, command.audit_actor)
        await _write_run_audit(
            db,
            command,
            run,
            "queue_failed",
            queued=False,
            error_code="BROKER_UNAVAILABLE",
        )
        await db.commit()
        raise OperationQueueUnavailable(run_id=run.id) from exc

    run = await transitions.mark_operation_queued(db, run.id, datetime.now(UTC))
    await _write_run_audit(db, command, run, "queued", queued=True)
    await db.commit()
    return OperationDispatch(run=run, replayed=False)
async def retry_operation_run(
    db: AsyncSession, retry: RetryCommand, task: DispatchTask
) -> OperationDispatch:
    previous = await db.get(OperationRun, retry.run_id)
    if previous is None or not transitions.is_terminal_state(previous.state):
        state = "MISSING" if previous is None else str(previous.state)
        raise transitions.OperationTransitionRejected(retry.run_id, state)
    dispatch = run_payloads.parse_stored_dispatch(previous.request_payload.get("_dispatch"))
    retry_key = run_keys.retry_operation_key(previous.id, retry.request_key)
    if retry_key is None:
        raise transitions.OperationTransitionRejected(previous.id, "MISSING_RETRY_KEY")
    command = OperationCommand(
        operation_type=previous.operation_type,
        hospital_id=previous.hospital_id,
        requested_by_id=retry.requested_by_id,
        idempotency_key=retry_key,
        audit_actor=retry.audit_actor,
        target_type=dispatch.target_type,
        target_id=dispatch.target_id,
        queue=dispatch.queue,
        task_args=dispatch.task_args,
        parent_run_id=previous.id,
    )
    return await dispatch_operation(db, command, task)


async def _find_idempotent_run(
    db: AsyncSession, command: OperationCommand
) -> OperationRun | None:
    key = run_keys.normalize_operation_key(command.idempotency_key)
    if key is None:
        return None
    actor_scope = (
        OperationRun.requested_by_id.is_(None)
        if command.requested_by_id is None
        else OperationRun.requested_by_id == command.requested_by_id
    )
    hospital_scope = (
        OperationRun.hospital_id.is_(None)
        if command.hospital_id is None
        else OperationRun.hospital_id == command.hospital_id
    )
    return await db.scalar(
        select(OperationRun).where(
            actor_scope,
            hospital_scope,
            OperationRun.operation_type == command.operation_type,
            OperationRun.idempotency_key == key,
        )
    )


async def _write_run_audit(
    db: AsyncSession,
    command: OperationCommand,
    run: OperationRun,
    event: str,
    *,
    queued: bool,
    error_code: str | None = None,
) -> None:
    action = command.operation_type.lower()
    suffix = "" if event == "queued" else f"_{event}"
    await write_audit_log(
        db,
        action=f"{action}{suffix}",
        hospital_id=command.hospital_id,
        actor=command.audit_actor,
        target_type="operation_run",
        target_id=run.id,
        detail={
            "queued": queued,
            "queue": command.queue,
            "operation_run_id": str(run.id),
            "source_type": command.target_type,
            "source_id": command.target_id,
            "task_id": run.task_id,
            "error_code": error_code,
        },
    )


async def _open_queue_incident(db: AsyncSession, run: OperationRun, actor: str) -> None:
    await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="operation_queue",
            object_type=run.operation_type,
            object_id=str(run.hospital_id or "global"),
            fingerprint=IncidentFingerprint.BROKER_UNAVAILABLE,
            incident_type="BROKER_UNAVAILABLE",
            severity=IncidentSeverity.HIGH,
            customer_impact="요청한 운영 작업이 시작되지 않았습니다.",
            source_type="operation_run",
            next_action="연결 상태를 확인한 뒤 작업을 다시 시도해 주세요.",
            admin_path="/operations",
            hospital_id=run.hospital_id,
            operation_run_id=run.id,
            source_id=str(run.id),
            safe_error_code="BROKER_UNAVAILABLE",
            safe_error_message=_BROKER_ERROR_MESSAGE,
        ),
        actor=actor,
        reason="broker dispatch rejected",
    )
