from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.hospital import Hospital
from app.models.operations import JSONValue, OperationRun
from app.services import operation_runs
from app.services.operation_runs import DispatchTask, OperationCommand, dispatch_operation
from app.workers import operation_run_signals

DATABASE_URL = "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"
SYNC_DATABASE_URL = "postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_test"


async def _skip_audit(
    db: AsyncSession,
    *,
    action: str,
    hospital_id: UUID | None,
    actor: str,
    target_type: str,
    target_id: str | UUID | None,
    detail: dict[str, JSONValue] | None,
) -> None:
    del db, action, hospital_id, actor, target_type, target_id, detail


@dataclass(frozen=True, slots=True)
class RecordingTask:
    calls: list[dict[str, str]] = field(default_factory=list)

    def apply_async(
        self,
        *,
        args: list[JSONValue],
        queue: str,
        headers: dict[str, str],
        task_id: str,
    ) -> SimpleNamespace:
        del args, queue
        self.calls.append({**headers, "task_id": task_id})
        return SimpleNamespace(id=task_id)


class InlineSuccessTask:
    def apply_async(
        self,
        *,
        args: list[JSONValue],
        queue: str,
        headers: dict[str, str],
        task_id: str,
    ) -> SimpleNamespace:
        del args, queue
        celery_task = SimpleNamespace(request=SimpleNamespace(headers=headers))
        operation_run_signals.track_operation_prerun(task_id=task_id, task=celery_task)
        operation_run_signals.track_operation_postrun(
            task_id=task_id,
            task=celery_task,
            state="SUCCESS",
        )
        return SimpleNamespace(id=task_id)


@pytest.fixture(name="signal_store")
async def signal_store(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], UUID]]:
    async_engine = create_async_engine(DATABASE_URL)
    async_factory = async_sessionmaker(async_engine, expire_on_commit=False)
    sync_engine = create_engine(SYNC_DATABASE_URL)
    sync_factory = sessionmaker(sync_engine, expire_on_commit=False, class_=Session)
    hospital_id = uuid4()
    async with async_factory() as db:
        db.add(Hospital(id=hospital_id, name="Signal Test", slug=f"signal-{hospital_id}"))
        await db.commit()
    monkeypatch.setattr(operation_runs, "write_audit_log", _skip_audit)
    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", sync_factory)
    try:
        yield async_factory, hospital_id
    finally:
        async with async_factory() as db:
            await db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
            await db.execute(delete(Hospital).where(Hospital.id == hospital_id))
            await db.commit()
        await async_engine.dispose()
        sync_engine.dispose()


async def dispatch_test_run(
    factory: async_sessionmaker[AsyncSession],
    hospital_id: UUID,
    task: DispatchTask,
    request_key: str,
) -> OperationRun:
    async with factory() as db:
        result = await dispatch_operation(
            db,
            OperationCommand(
                hospital_id=hospital_id,
                operation_type="REBUILD_SITE",
                idempotency_key=request_key,
                requested_by_id=None,
                audit_actor="system@motionlabs.kr",
                target_type="hospital",
                target_id=str(hospital_id),
                queue="content_generation",
                task_args=(str(hospital_id),),
            ),
            task,
        )
        return result.run
