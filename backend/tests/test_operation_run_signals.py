from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.operations import OperationRun, OperationRunState
from app.services.operation_runs import OperationCommand, dispatch_operation
from app.workers import operation_run_signals
from tests.operation_run_signal_support import (
    InlineSuccessTask,
    RecordingTask,
    dispatch_test_run,
)
from tests.operation_run_signal_support import (
    signal_store as _signal_store_fixture,  # noqa: F401
)


@pytest.mark.asyncio
async def test_signal_adapter_moves_dispatched_run_to_success(
    signal_store: tuple[async_sessionmaker[AsyncSession], UUID],
) -> None:
    factory, hospital_id = signal_store
    task = RecordingTask()
    run = await dispatch_test_run(factory, hospital_id, task, "signal-success")

    assert task.calls == [
        {"operation_run_id": str(run.id), "task_id": run.task_id},
    ]
    celery_task = SimpleNamespace(
        request=SimpleNamespace(headers={"operation_run_id": str(run.id)})
    )
    operation_run_signals.track_operation_prerun(
        task_id=run.task_id,
        task=celery_task,
    )
    operation_run_signals.track_operation_postrun(
        task_id=run.task_id,
        task=celery_task,
        state="SUCCESS",
    )

    async with factory() as db:
        stored = await db.scalar(select(OperationRun).where(OperationRun.id == run.id))
    assert stored is not None
    assert stored.state == OperationRunState.SUCCEEDED.value
    assert stored.completed_at is not None
    assert stored.lease_owner is None


@pytest.mark.asyncio
async def test_worker_can_finish_before_broker_acceptance_cas_without_regression(
    signal_store: tuple[async_sessionmaker[AsyncSession], UUID],
) -> None:
    factory, hospital_id = signal_store

    async with factory() as db:
        result = await dispatch_operation(
            db,
            OperationCommand(
                hospital_id=hospital_id,
                operation_type="REBUILD_SITE",
                idempotency_key="signal-inline-success",
                requested_by_id=None,
                audit_actor="system@motionlabs.kr",
                target_type="hospital",
                target_id=str(hospital_id),
                queue="content_generation",
                task_args=(str(hospital_id),),
            ),
            InlineSuccessTask(),
        )

    assert result.run.state == OperationRunState.SUCCEEDED.value
    async with factory() as db:
        stored = await db.get(OperationRun, result.run.id)
    assert stored is not None
    assert stored.state == OperationRunState.SUCCEEDED.value
    assert stored.completed_at is not None


@pytest.mark.asyncio
async def test_signal_failure_is_terminal_and_never_persists_exception_text(
    signal_store: tuple[async_sessionmaker[AsyncSession], UUID],
) -> None:
    factory, hospital_id = signal_store
    task = RecordingTask()
    run = await dispatch_test_run(factory, hospital_id, task, "signal-failure")
    celery_task = SimpleNamespace(
        request=SimpleNamespace(headers={"operation_run_id": str(run.id)})
    )
    operation_run_signals.track_operation_prerun(
        task_id=run.task_id,
        task=celery_task,
    )
    operation_run_signals.track_operation_failure(
        task_id=run.task_id,
        task=celery_task,
        exception=RuntimeError("patient-secret@example.com"),
    )
    operation_run_signals.track_operation_postrun(
        task_id=run.task_id,
        task=celery_task,
        state="FAILURE",
    )

    async with factory() as db:
        stored = await db.scalar(select(OperationRun).where(OperationRun.id == run.id))
    assert stored is not None
    assert stored.state == OperationRunState.FAILED.value
    assert stored.safe_error_code == "TASK_FAILED"
    assert "patient-secret" not in (stored.safe_error_message or "")
    assert stored.lease_owner is None


@pytest.mark.asyncio
async def test_retry_signal_requeues_then_next_attempt_can_finish(
    signal_store: tuple[async_sessionmaker[AsyncSession], UUID],
) -> None:
    factory, hospital_id = signal_store
    task = RecordingTask()
    run = await dispatch_test_run(factory, hospital_id, task, "signal-retry")
    celery_task = SimpleNamespace(
        request=SimpleNamespace(headers={"operation_run_id": str(run.id)})
    )
    operation_run_signals.track_operation_prerun(task_id=run.task_id, task=celery_task)
    operation_run_signals.track_operation_postrun(
        task_id=run.task_id,
        task=celery_task,
        state="RETRY",
    )

    async with factory() as db:
        waiting = await db.get(OperationRun, run.id)
    assert waiting is not None
    assert waiting.state == OperationRunState.QUEUED.value
    assert waiting.lease_owner is None

    operation_run_signals.track_operation_prerun(task_id=run.task_id, task=celery_task)
    operation_run_signals.track_operation_postrun(
        task_id=run.task_id,
        task=celery_task,
        state="SUCCESS",
    )
    async with factory() as db:
        finished = await db.get(OperationRun, run.id)
    assert finished is not None
    assert finished.state == OperationRunState.SUCCEEDED.value
    assert finished.attempt_count == 2


@pytest.mark.asyncio
async def test_duplicate_delivery_cannot_finish_until_expired_lease_is_reclaimed(
    signal_store: tuple[async_sessionmaker[AsyncSession], UUID],
) -> None:
    factory, hospital_id = signal_store
    task = RecordingTask()
    run = await dispatch_test_run(factory, hospital_id, task, "signal-redelivery")
    headers = {"operation_run_id": str(run.id)}
    first = SimpleNamespace(request=SimpleNamespace(headers=headers))
    duplicate = SimpleNamespace(request=SimpleNamespace(headers=headers))
    operation_run_signals.track_operation_prerun(task_id=run.task_id, task=first)
    operation_run_signals.track_operation_prerun(task_id=run.task_id, task=duplicate)

    assert first.request.operation_run_claim_version is not None
    assert duplicate.request.operation_run_claim_version is None
    operation_run_signals.track_operation_postrun(
        task_id=run.task_id,
        task=duplicate,
        state="SUCCESS",
    )
    async with factory() as db:
        still_running = await db.get(OperationRun, run.id)
    assert still_running is not None
    assert still_running.state == OperationRunState.RUNNING.value

    async with factory() as db:
        await db.execute(
            update(OperationRun)
            .where(OperationRun.id == run.id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
    replacement = SimpleNamespace(request=SimpleNamespace(headers=headers))
    operation_run_signals.track_operation_prerun(task_id=run.task_id, task=replacement)
    assert replacement.request.operation_run_claim_version is not None
    assert (
        replacement.request.operation_run_claim_version
        > first.request.operation_run_claim_version
    )

    operation_run_signals.track_operation_postrun(
        task_id=run.task_id,
        task=first,
        state="SUCCESS",
    )
    async with factory() as db:
        reclaimed = await db.get(OperationRun, run.id)
    assert reclaimed is not None
    assert reclaimed.state == OperationRunState.RUNNING.value
    operation_run_signals.track_operation_postrun(
        task_id=run.task_id,
        task=replacement,
        state="SUCCESS",
    )
    async with factory() as db:
        finished = await db.get(OperationRun, run.id)
    assert finished is not None
    assert finished.state == OperationRunState.SUCCEEDED.value


@pytest.mark.asyncio
async def test_broker_marked_worker_loss_redelivery_reclaims_the_same_run(
    signal_store: tuple[async_sessionmaker[AsyncSession], UUID],
) -> None:
    factory, hospital_id = signal_store
    task = RecordingTask()
    run = await dispatch_test_run(factory, hospital_id, task, "signal-worker-loss")
    headers = {"operation_run_id": str(run.id)}
    lost_worker = SimpleNamespace(request=SimpleNamespace(headers=headers))
    redelivery = SimpleNamespace(
        request=SimpleNamespace(
            headers=headers,
            delivery_info={"redelivered": True},
        )
    )
    operation_run_signals.track_operation_prerun(
        task_id=run.task_id,
        task=lost_worker,
    )
    operation_run_signals.track_operation_prerun(
        task_id=run.task_id,
        task=redelivery,
    )

    assert redelivery.request.operation_run_claim_version is not None
    assert (
        redelivery.request.operation_run_claim_version
        > lost_worker.request.operation_run_claim_version
    )
    operation_run_signals.track_operation_postrun(
        task_id=run.task_id,
        task=lost_worker,
        state="SUCCESS",
    )
    async with factory() as db:
        still_running = await db.get(OperationRun, run.id)
    assert still_running is not None
    assert still_running.state == OperationRunState.RUNNING.value

    operation_run_signals.track_operation_postrun(
        task_id=run.task_id,
        task=redelivery,
        state="SUCCESS",
    )
    async with factory() as db:
        finished = await db.get(OperationRun, run.id)
    assert finished is not None
    assert finished.state == OperationRunState.SUCCEEDED.value
