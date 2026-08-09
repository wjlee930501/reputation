"""Concurrent PostgreSQL proof for broker-failure incident deduplication."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Never

import anyio
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.operations import Incident, JSONValue, OperationRun, OperationRunState
from app.services import incidents as incident_service
from app.services import operation_runs as operation_run_service
from app.services.operation_runs import (
    OperationCommand,
    OperationQueueUnavailable,
    RetryCommand,
    dispatch_operation,
    retry_operation_run,
)

_DATABASE_URL = (
    "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"
)


class FailingTask:
    def apply_async(
        self,
        *,
        args: list[JSONValue],
        queue: str,
        headers: dict[str, str],
        task_id: str,
    ) -> SimpleNamespace:
        del args, queue, headers, task_id
        raise ConnectionError("broker unavailable")


class RecordingTask:
    def __init__(self) -> None:
        self.calls: list[list[JSONValue]] = []

    def apply_async(
        self,
        *,
        args: list[JSONValue],
        queue: str,
        headers: dict[str, str],
        task_id: str,
    ) -> SimpleNamespace:
        del queue, headers
        self.calls.append(args)
        return SimpleNamespace(id=task_id)


def _command(hospital_id: uuid.UUID, key: str) -> OperationCommand:
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


async def _no_audit(*_args: Never, **_kwargs: Never) -> None:
    return None


@pytest.mark.asyncio
async def test_concurrent_broker_failures_share_one_atomic_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two independently committed commands for the same operation scope
    engine = create_async_engine(_DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    async with sessions() as setup:
        await setup.execute(
            text("INSERT INTO hospitals (id, name, slug) VALUES (:id, :name, :slug)"),
            {
                "id": hospital_id,
                "name": "Concurrent Broker QA Clinic",
                "slug": f"concurrent-broker-{hospital_id.hex}",
            },
        )
        await setup.commit()
    monkeypatch.setattr(operation_run_service, "write_audit_log", _no_audit)
    monkeypatch.setattr(incident_service, "_audit", _no_audit)
    failed_run_ids: list[uuid.UUID] = []

    async def fail_one(key: str) -> None:
        async with sessions() as db:
            with pytest.raises(OperationQueueUnavailable) as raised:
                await dispatch_operation(db, _command(hospital_id, key), FailingTask())
            failed_run_ids.append(raised.value.run_id)

    try:
        # When: both broker failures race through the stable incident key
        async with anyio.create_task_group() as group:
            group.start_soon(fail_one, "OPS-QA-CONCURRENT-A")
            group.start_soon(fail_one, "OPS-QA-CONCURRENT-B")

        # Then: atomic upsert keeps one incident and both truthful FAILED runs
        async with sessions() as verify:
            incidents = list(
                await verify.scalars(
                    select(Incident).where(Incident.hospital_id == hospital_id)
                )
            )
            failed_count = await verify.scalar(
                select(func.count(OperationRun.id)).where(
                    OperationRun.id.in_(failed_run_ids),
                    OperationRun.state == OperationRunState.FAILED,
                )
            )
            assert len(incidents) == 1
            assert incidents[0].occurrence_count == 2
            assert failed_count == 2
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                text("DELETE FROM incidents WHERE hospital_id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await cleanup.execute(
                text("DELETE FROM operation_runs WHERE hospital_id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await cleanup.execute(
                text("DELETE FROM hospitals WHERE id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await cleanup.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_same_retry_key_creates_and_dispatches_one_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one failed parent run and two HTTP retries carrying the same request key
    engine = create_async_engine(_DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    async with sessions() as setup:
        await setup.execute(
            text("INSERT INTO hospitals (id, name, slug) VALUES (:id, :name, :slug)"),
            {
                "id": hospital_id,
                "name": "Concurrent Retry QA Clinic",
                "slug": f"concurrent-retry-{hospital_id.hex}",
            },
        )
        setup.add(
            OperationRun(
                id=parent_id,
                hospital_id=hospital_id,
                operation_type="REGENERATE_CONTENT",
                state=OperationRunState.FAILED,
                idempotency_key="OPS-QA-PARENT",
                request_payload={
                    "source_type": "hospital",
                    "source_id": str(hospital_id),
                    "_dispatch": {
                        "target_type": "hospital",
                        "target_id": str(hospital_id),
                        "queue": "content",
                        "task_args": [str(hospital_id)],
                    },
                },
                completed_at=datetime.now(UTC),
                attempt_count=0,
                total_count=0,
                success_count=0,
                failure_count=0,
                skipped_count=0,
                version=1,
            )
        )
        await setup.commit()
    monkeypatch.setattr(operation_run_service, "write_audit_log", _no_audit)
    task = RecordingTask()
    children: list[uuid.UUID] = []

    async def retry_once() -> None:
        async with sessions() as db:
            result = await retry_operation_run(
                db,
                RetryCommand(
                    run_id=parent_id,
                    requested_by_id=None,
                    audit_actor="qa@example.test",
                    request_key="OPS-QA-RETRY-RACE",
                ),
                task,
            )
            children.append(result.run.id)

    try:
        # When: the retries race before either caller can observe the child
        async with anyio.create_task_group() as group:
            group.start_soon(retry_once)
            group.start_soon(retry_once)

        # Then: unique scope replay returns one child and broker dispatch happens once
        async with sessions() as verify:
            child_count = await verify.scalar(
                select(func.count(OperationRun.id)).where(
                    OperationRun.parent_run_id == parent_id
                )
            )
            assert len(set(children)) == 1
            assert child_count == 1
            assert len(task.calls) == 1
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                text("DELETE FROM operation_runs WHERE hospital_id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await cleanup.execute(
                text("DELETE FROM hospitals WHERE id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await cleanup.commit()
        await engine.dispose()
