from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.models.hospital import Hospital
from app.models.operations import JSONValue, OperationRun, OperationRunState
from app.services import operation_runs
from app.services.operation_runs import DispatchTask, OperationCommand, dispatch_operation
from app.workers import operation_run_signals
from tests.db_env import require_db_urls

_ASYNC_URL_ENV = "OPERATION_RUN_SIGNAL_DATABASE_URL"
_SYNC_URL_ENV = "OPERATION_RUN_SIGNAL_SYNC_DATABASE_URL"
# No default: None when unset. Every test that connects with these goes through the
# `signal_store` fixture, which fails first (naming the missing variable) in that case.
DATABASE_URL = os.getenv(_ASYNC_URL_ENV)
SYNC_DATABASE_URL = os.getenv(_SYNC_URL_ENV)

# Keep the required-Postgres probe bounded while tolerating transient Docker I/O
# pressure during the full suite. A failed endpoint still fails; it is never skipped.
_PROBE_CONNECT_TIMEOUT = 10


def _unavailable(reason: str) -> None:
    """The signal-store DB is always required: an unreachable one fails, never skips."""
    pytest.fail(
        f"{_ASYNC_URL_ENV} and {_SYNC_URL_ENV} are set, so the signal-store Postgres is "
        f"required and must not be skipped: {reason}",
        pytrace=False,
    )


async def _require_postgres_reachable() -> tuple[str, str]:
    """Return both signal-store URLs, failing when either is unset or cannot be connected to.

    Read at call time, not import time. Only a failed connection is handled here; once
    connected, schema or query errors still fail on their own.
    """
    database_url, sync_database_url = require_db_urls(_ASYNC_URL_ENV, _SYNC_URL_ENV)
    sync_probe = create_engine(
        sync_database_url,
        poolclass=NullPool,
        connect_args={"connect_timeout": _PROBE_CONNECT_TIMEOUT},
    )
    async_probe = create_async_engine(
        database_url,
        poolclass=NullPool,
        connect_args={"timeout": _PROBE_CONNECT_TIMEOUT},
    )
    try:
        with sync_probe.connect():
            pass
        async with async_probe.connect():
            pass
    except (OSError, OperationalError) as exc:
        _unavailable(f"PostgreSQL unreachable: {type(exc).__name__}")
    finally:
        sync_probe.dispose()
        await async_probe.dispose()
    return database_url, sync_database_url


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
    database_url, sync_database_url = await _require_postgres_reachable()
    async_engine = create_async_engine(database_url)
    async_factory = async_sessionmaker(async_engine, expire_on_commit=False)
    sync_engine = create_engine(sync_database_url)
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
    operation_type: str = "REBUILD_SITE",
) -> OperationRun:
    async with factory() as db:
        result = await dispatch_operation(
            db,
            OperationCommand(
                hospital_id=hospital_id,
                operation_type=operation_type,
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


def seed_closed_monthly_sov_run(
    hospital_id: UUID,
    period_key: str,
    state: OperationRunState,
    *,
    safe_error_code: str,
    version: int = 3,
) -> OperationRun:
    """Commit a finished monthly RUN_SOV row (PARTIAL/FAILED) through the signal store."""
    finished_at = datetime(2026, 8, 27, tzinfo=UTC)
    run = OperationRun(
        id=uuid4(),
        hospital_id=hospital_id,
        operation_type="RUN_SOV",
        state=state,
        idempotency_key=f"monthly-sov:{hospital_id}:{period_key}",
        task_id=str(uuid4()),
        attempt_count=1,
        total_count=1,
        success_count=0,
        failure_count=1,
        skipped_count=0,
        request_payload={},
        queued_at=finished_at,
        started_at=finished_at,
        completed_at=finished_at,
        lease_owner="worker",
        lease_expires_at=finished_at,
        safe_error_code=safe_error_code,
        safe_error_message="failed",
        version=version,
    )
    with operation_run_signals.SyncSessionLocal() as db:
        db.add(run)
        db.commit()
    return run


def stored_operation_run(run_id: UUID) -> OperationRun:
    with operation_run_signals.SyncSessionLocal() as db:
        return db.execute(select(OperationRun).where(OperationRun.id == run_id)).scalar_one()


def count_commits(db: Session) -> list[None]:
    """Record each commit the code under test issues on ``db``."""
    commits: list[None] = []
    commit = db.commit

    def _counted_commit() -> None:
        commits.append(None)
        commit()

    db.commit = _counted_commit
    return commits
