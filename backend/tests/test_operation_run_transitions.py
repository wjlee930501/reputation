"""Lease and terminal compare-and-swap proofs for OperationRun."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.operations import JSONValue, OperationRunState
from app.services.operation_runs import (
    Heartbeat,
    LeaseClaim,
    OperationCommand,
    OperationDispatch,
    TerminalTransition,
    claim_operation_run,
    dispatch_operation,
    finish_operation_run,
    heartbeat_operation_run,
)

_DATABASE_URL = (
    "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"
)


class RecordingTask:
    def apply_async(
        self,
        *,
        args: list[JSONValue],
        queue: str,
        headers: dict[str, str],
        task_id: str,
    ) -> SimpleNamespace:
        del args, queue, headers
        return SimpleNamespace(id=task_id)


@pytest.fixture
async def operation_db() -> AsyncSession:
    engine = create_async_engine(_DATABASE_URL)
    connection = await engine.connect()
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


async def _queued_run(db: AsyncSession, key: str) -> OperationDispatch:
    hospital_id = uuid.uuid4()
    await db.execute(
        text("INSERT INTO hospitals (id, name, slug) VALUES (:id, :name, :slug)"),
        {
            "id": hospital_id,
            "name": "Operation Transition QA Clinic",
            "slug": f"operation-transition-{hospital_id.hex}",
        },
    )
    await db.commit()
    return await dispatch_operation(
        db,
        OperationCommand(
            operation_type="REGENERATE_CONTENT",
            hospital_id=hospital_id,
            requested_by_id=None,
            idempotency_key=key,
            audit_actor="qa@example.test",
            target_type="hospital",
            target_id=str(hospital_id),
            queue="content",
            task_args=(str(hospital_id),),
        ),
        RecordingTask(),
    )


@pytest.mark.asyncio
async def test_stale_lease_is_reclaimed_and_old_worker_cannot_overwrite_success(
    operation_db: AsyncSession,
) -> None:
    # Given: a queued run claimed by a worker whose lease later expires
    queued = await _queued_run(operation_db, "OPS-QA-LEASE")
    started_at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    old_claim = await claim_operation_run(
        operation_db,
        LeaseClaim(queued.run.id, "worker-old", 30, started_at),
    )
    assert old_claim is not None

    # When: the expired owner cannot resurrect; a new worker reclaims and completes
    expired_heartbeat = await heartbeat_operation_run(
        operation_db,
        Heartbeat(
            queued.run.id,
            "worker-old",
            old_claim.version,
            30,
            started_at + timedelta(seconds=31),
        ),
    )
    new_claim = await claim_operation_run(
        operation_db,
        LeaseClaim(
            queued.run.id,
            "worker-new",
            30,
            started_at + timedelta(seconds=31),
        ),
    )
    assert new_claim is not None
    succeeded = await finish_operation_run(
        operation_db,
        TerminalTransition(
            run_id=queued.run.id,
            worker_id="worker-new",
            expected_version=new_claim.version,
            state=OperationRunState.SUCCEEDED,
            completed_at=started_at + timedelta(seconds=32),
        ),
    )
    late = await finish_operation_run(
        operation_db,
        TerminalTransition(
            run_id=queued.run.id,
            worker_id="worker-old",
            expected_version=old_claim.version,
            state=OperationRunState.FAILED,
            completed_at=started_at + timedelta(seconds=33),
            safe_error_code="LATE_FAILURE",
            safe_error_message="late worker result",
        ),
    )

    # Then: the stale write loses and the success remains authoritative
    assert expired_heartbeat is None
    assert succeeded.changed is True
    assert late.changed is False
    assert late.run.state == OperationRunState.SUCCEEDED
    await operation_db.refresh(late.run)
    assert late.run.safe_error_code is None


@pytest.mark.asyncio
async def test_heartbeat_extends_only_the_matching_worker_version(
    operation_db: AsyncSession,
) -> None:
    # Given: one live worker lease
    queued = await _queued_run(operation_db, "OPS-QA-HEARTBEAT")
    started_at = datetime(2026, 8, 10, 13, 0, tzinfo=UTC)
    claimed = await claim_operation_run(
        operation_db,
        LeaseClaim(queued.run.id, "worker-live", 30, started_at),
    )
    assert claimed is not None
    claim_version = claimed.version

    # When: the owner heartbeats once, then replays the stale version
    renewed = await heartbeat_operation_run(
        operation_db,
        Heartbeat(
            queued.run.id,
            "worker-live",
            claim_version,
            30,
            started_at + timedelta(seconds=10),
        ),
    )
    stale = await heartbeat_operation_run(
        operation_db,
        Heartbeat(
            queued.run.id,
            "worker-live",
            claim_version,
            30,
            started_at + timedelta(seconds=11),
        ),
    )

    # Then: only the CAS-matching heartbeat moves the lease forward
    assert renewed is not None
    assert renewed.lease_expires_at == started_at + timedelta(seconds=40)
    assert stale is None
