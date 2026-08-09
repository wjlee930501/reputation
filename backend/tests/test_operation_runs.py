"""Real-Postgres contract tests for truthful operational commands."""

import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.audit import AdminAuditLog
from app.models.operations import Incident, JSONValue, OperationRun, OperationRunState
from app.services.operation_run_keys import retry_operation_key
from app.services.operation_runs import (
    OperationCommand,
    OperationQueueUnavailable,
    RetryCommand,
    UnsafeDispatchArgument,
    dispatch_operation,
    retry_operation_run,
)

_DATABASE_URL = (
    "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"
)


class RecordingTask:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.calls: list[dict[str, JSONValue]] = []
        self.failure = failure

    def apply_async(
        self,
        *,
        args: list[JSONValue],
        queue: str,
        headers: dict[str, str],
        task_id: str,
    ) -> SimpleNamespace:
        del headers
        self.calls.append({"args": args, "queue": queue})
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(id=task_id)


@pytest.fixture
async def operation_db() -> AsyncSession:
    engine = create_async_engine(_DATABASE_URL)
    try:
        connection = await engine.connect()
    except OSError as exc:
        await engine.dispose()
        pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def _hospital_id(db: AsyncSession) -> uuid.UUID:
    hospital_id = uuid.uuid4()
    await db.execute(
        text("INSERT INTO hospitals (id, name, slug) VALUES (:id, :name, :slug)"),
        {
            "id": hospital_id,
            "name": "OperationRun QA Clinic",
            "slug": f"operation-run-{hospital_id.hex}",
        },
    )
    await db.commit()
    return hospital_id


def _command(hospital_id: uuid.UUID, *, key: str) -> OperationCommand:
    return OperationCommand(
        operation_type="REGENERATE_CONTENT",
        hospital_id=hospital_id,
        requested_by_id=None,
        idempotency_key=key,
        audit_actor="qa@example.test",
        target_type="hospital",
        target_id=str(hospital_id),
        queue="content",
        task_args=(str(hospital_id),),
    )


@pytest.mark.asyncio
async def test_duplicate_command_returns_original_run_without_redispatch(
    operation_db: AsyncSession,
) -> None:
    # Given: one client command with a stable idempotency key
    hospital_id = await _hospital_id(operation_db)
    command = _command(hospital_id, key="OPS-QA-20260810-RUN-1")
    task = RecordingTask()

    # When: the same command reaches the service twice
    first = await dispatch_operation(operation_db, command, task)
    second = await dispatch_operation(operation_db, command, task)

    # Then: the durable original is replayed and the broker sees one message
    assert second.run.id == first.run.id
    assert second.replayed is True
    assert first.run.state == OperationRunState.QUEUED
    assert task.calls == [{"args": [str(hospital_id)], "queue": "content"}]
    assert await operation_db.scalar(
        select(func.count(OperationRun.id)).where(
            OperationRun.idempotency_key == command.idempotency_key
        )
    ) == 1
    assert await operation_db.scalar(
        select(func.count(AdminAuditLog.id)).where(
            AdminAuditLog.target_id == str(first.run.id)
        )
    ) == 2


@pytest.mark.asyncio
async def test_broker_failure_is_failed_run_open_incident_and_safe_audit(
    operation_db: AsyncSession,
) -> None:
    # Given: a broker exception containing text that must never reach storage
    hospital_id = await _hospital_id(operation_db)
    command = _command(hospital_id, key="OPS-QA-BROKER-FAIL")
    task = RecordingTask(failure=ConnectionError("redis://user:secret@broker"))

    # When: dispatch fails after the request transaction is committed
    with pytest.raises(OperationQueueUnavailable) as raised:
        await dispatch_operation(operation_db, command, task)

    # Then: operators see a durable FAILED run and OPEN incident, never a queued claim
    run = await operation_db.get(OperationRun, raised.value.run_id)
    incident = await operation_db.scalar(
        select(Incident).where(Incident.operation_run_id == raised.value.run_id)
    )
    actions = list(
        await operation_db.scalars(
            select(AdminAuditLog.action)
            .where(AdminAuditLog.target_id == str(raised.value.run_id))
            .order_by(AdminAuditLog.created_at)
        )
    )
    assert run is not None
    assert run.state == OperationRunState.FAILED
    assert run.safe_error_code == "BROKER_UNAVAILABLE"
    assert "secret" not in (run.safe_error_message or "")
    assert incident is not None and incident.state == "OPEN"
    assert set(actions) == {
        "regenerate_content_requested",
        "regenerate_content_queue_failed",
    }


@pytest.mark.asyncio
async def test_retry_creates_linked_run_with_server_key(operation_db: AsyncSession) -> None:
    # Given: a terminal failed run
    hospital_id = await _hospital_id(operation_db)
    original_command = _command(hospital_id, key="OPS-QA-FAILED-ORIGINAL")
    with pytest.raises(OperationQueueUnavailable) as raised:
        await dispatch_operation(
            operation_db,
            original_command,
            RecordingTask(failure=ConnectionError("broker down")),
        )

    # When: an operator explicitly retries it
    task = RecordingTask()
    retried = await retry_operation_run(
        operation_db,
        RetryCommand(
            run_id=raised.value.run_id,
            requested_by_id=None,
            audit_actor="qa@example.test",
            request_key="OPS-QA-RETRY-1",
        ),
        task,
    )

    # Then: a new attempt links backward and never reuses the client key
    assert retried.run.id != raised.value.run_id
    assert retried.run.parent_run_id == raised.value.run_id
    assert retried.run.idempotency_key == (
        f"retry:{raised.value.run_id}:OPS-QA-RETRY-1"
    )
    assert retried.run.state == OperationRunState.QUEUED
    assert task.calls == [{"args": [str(hospital_id)], "queue": "content"}]


@pytest.mark.asyncio
async def test_different_retry_request_key_creates_another_child(
    operation_db: AsyncSession,
) -> None:
    # Given: one failed parent run
    hospital_id = await _hospital_id(operation_db)
    with pytest.raises(OperationQueueUnavailable) as raised:
        await dispatch_operation(
            operation_db,
            _command(hospital_id, key="OPS-QA-RETRY-SCOPE-PARENT"),
            RecordingTask(failure=ConnectionError("broker down")),
        )
    task = RecordingTask()

    # When: two explicit retries use different request keys
    children = []
    for request_key in ("OPS-QA-RETRY-A", "OPS-QA-RETRY-B"):
        children.append(
            await retry_operation_run(
                operation_db,
                RetryCommand(
                    run_id=raised.value.run_id,
                    requested_by_id=None,
                    audit_actor="qa@example.test",
                    request_key=request_key,
                ),
                task,
            )
        )

    # Then: each intentional retry has its own child and broker dispatch
    assert children[0].run.id != children[1].run.id
    assert children[0].run.parent_run_id == raised.value.run_id
    assert children[1].run.parent_run_id == raised.value.run_id
    assert len(task.calls) == 2


@pytest.mark.asyncio
async def test_dispatch_payload_rejects_raw_contact_or_secret_text(
    operation_db: AsyncSession,
) -> None:
    # Given: a future adapter accidentally passes contact text as a Celery argument
    hospital_id = await _hospital_id(operation_db)
    unsafe = replace(
        _command(hospital_id, key="OPS-QA-UNSAFE-ARG"),
        task_args=("director@example.test",),
    )
    task = RecordingTask()

    # When: the command crosses the durable dispatch boundary
    with pytest.raises(UnsafeDispatchArgument):
        await dispatch_operation(operation_db, unsafe, task)

    # Then: no run, audit, or broker message can contain the unsafe value
    assert task.calls == []
    assert await operation_db.scalar(
        select(func.count(OperationRun.id)).where(
            OperationRun.idempotency_key == "OPS-QA-UNSAFE-ARG"
        )
    ) == 0


def test_retry_key_hashes_long_client_key_deterministically() -> None:
    # Given: the longest accepted HTTP idempotency key
    parent_id = uuid.uuid4()
    request_key = "x" * 300

    # When: the server derives the child scope twice
    first = retry_operation_key(parent_id, request_key)
    replay = retry_operation_key(parent_id, request_key)

    # Then: it remains index-safe and stable without storing the long raw key
    assert first == replay
    assert first is not None and len(first) <= 255
    assert first.startswith(f"retry:{parent_id}:sha256:")
    assert request_key not in first
    assert retry_operation_key(parent_id, f"{request_key}a") != first
