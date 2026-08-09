"""Celery signal bridge for durable OperationRun lifecycle truth."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, assert_never
from uuid import UUID

from celery.signals import task_failure, task_postrun, task_prerun
from sqlalchemy import and_, func, or_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.dml import Update

from app.core.database import SyncSessionLocal
from app.models.operations import JSONValue, OperationRun, OperationRunState

logger = logging.getLogger(__name__)

_HEADER = "operation_run_id"
_LEASE_SECONDS = 60 * 60
_TASK_FAILED_MESSAGE = "작업 실행 중 오류가 발생했습니다. 운영 관제에서 다시 시도해 주세요."
_TASK_REVOKED_MESSAGE = "작업 실행이 취소되었습니다."


class _SignalRequest(Protocol):
    headers: Mapping[str, str] | None
    operation_run_claim_version: int | None


class _SignalTask(Protocol):
    request: _SignalRequest


class _PostrunState(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REVOKED = "REVOKED"
    RETRY = "RETRY"


@task_prerun.connect(weak=False)
def track_operation_prerun(
    *,
    task_id: str | None = None,
    task: _SignalTask | None = None,
    **_kwargs: JSONValue,
) -> None:
    run_id = _run_id_from_task(task)
    worker_id = _worker_id(task_id)
    if run_id is None or worker_id is None:
        return
    now = datetime.now(UTC)
    task.request.operation_run_claim_version = _claim_safely(run_id, worker_id, now)


def _claim_safely(run_id: UUID, worker_id: str, now: datetime) -> int | None:
    queued = (
        update(OperationRun)
        .where(
            OperationRun.id == run_id,
            OperationRun.task_id == worker_id,
            OperationRun.state == OperationRunState.REQUESTED,
        )
        .values(
            state=OperationRunState.QUEUED,
            queued_at=now,
            version=OperationRun.version + 1,
        )
    )
    running = (
        update(OperationRun)
        .where(
            OperationRun.id == run_id,
            OperationRun.task_id == worker_id,
            or_(
                OperationRun.state == OperationRunState.QUEUED,
                and_(
                    OperationRun.state == OperationRunState.RUNNING,
                    OperationRun.lease_expires_at <= now,
                ),
            ),
        )
        .values(
            state=OperationRunState.RUNNING,
            queued_at=func.coalesce(OperationRun.queued_at, now),
            started_at=func.coalesce(OperationRun.started_at, now),
            heartbeat_at=now,
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=_LEASE_SECONDS),
            attempt_count=OperationRun.attempt_count + 1,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun.version)
    )
    try:
        with SyncSessionLocal() as db:
            db.execute(queued)
            claimed_version = db.execute(running).scalar_one_or_none()
            db.commit()
            return claimed_version
    except SQLAlchemyError as exc:
        _log_database_error("claim", run_id, exc)
        return None


@task_failure.connect(weak=False)
def track_operation_failure(
    *,
    task_id: str | None = None,
    task: _SignalTask | None = None,
    sender: _SignalTask | None = None,
    exception: BaseException | None = None,
    **_kwargs: JSONValue,
) -> None:
    del exception
    _finish_from_signal(
        task_id,
        task if task is not None else sender,
        OperationRunState.FAILED,
        "TASK_FAILED",
        _TASK_FAILED_MESSAGE,
    )


@task_postrun.connect(weak=False)
def track_operation_postrun(
    *,
    task_id: str | None = None,
    task: _SignalTask | None = None,
    state: str | None = None,
    **_kwargs: JSONValue,
) -> None:
    try:
        outcome = _PostrunState(state)
    except ValueError:
        return
    match outcome:
        case _PostrunState.SUCCESS:
            _finish_from_signal(
                task_id, task, OperationRunState.SUCCEEDED, None, None
            )
        case _PostrunState.FAILURE:
            _finish_from_signal(
                task_id,
                task,
                OperationRunState.FAILED,
                "TASK_FAILED",
                _TASK_FAILED_MESSAGE,
            )
        case _PostrunState.REVOKED:
            _finish_from_signal(
                task_id,
                task,
                OperationRunState.CANCELLED,
                "TASK_REVOKED",
                _TASK_REVOKED_MESSAGE,
            )
        case _PostrunState.RETRY:
            _requeue_from_signal(task_id, task)
        case unreachable:
            assert_never(unreachable)


def _finish_from_signal(
    task_id: str | None,
    task: _SignalTask | None,
    state: OperationRunState,
    safe_error_code: str | None,
    safe_error_message: str | None,
) -> None:
    run_id = _run_id_from_task(task)
    worker_id = _worker_id(task_id)
    expected_version = _claim_version(task)
    if run_id is None or worker_id is None or expected_version is None:
        return
    statement = (
        update(OperationRun)
        .where(
            OperationRun.id == run_id,
            OperationRun.task_id == worker_id,
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.lease_owner == worker_id,
            OperationRun.version == expected_version,
        )
        .values(
            state=state,
            completed_at=datetime.now(UTC),
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            safe_error_code=safe_error_code,
            safe_error_message=safe_error_message,
            version=OperationRun.version + 1,
        )
    )
    _execute_safely(statement, run_id, "finish")


def _requeue_from_signal(task_id: str | None, task: _SignalTask | None) -> None:
    run_id = _run_id_from_task(task)
    worker_id = _worker_id(task_id)
    expected_version = _claim_version(task)
    if run_id is None or worker_id is None or expected_version is None:
        return
    statement = (
        update(OperationRun)
        .where(
            OperationRun.id == run_id,
            OperationRun.task_id == worker_id,
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.lease_owner == worker_id,
            OperationRun.version == expected_version,
        )
        .values(
            state=OperationRunState.QUEUED,
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            version=OperationRun.version + 1,
        )
    )
    _execute_safely(statement, run_id, "retry")


def _execute_safely(statement: Update, run_id: UUID, phase: str) -> None:
    try:
        with SyncSessionLocal() as db:
            db.execute(statement)
            db.commit()
    except SQLAlchemyError as exc:
        _log_database_error(phase, run_id, exc)


def _log_database_error(phase: str, run_id: UUID, exc: SQLAlchemyError) -> None:
    logger.warning(
        "operation run lifecycle %s unavailable run_id=%s error=%s",
        phase,
        run_id,
        type(exc).__name__,
    )


def _run_id_from_task(task: _SignalTask | None) -> UUID | None:
    if task is None:
        return None
    headers = task.request.headers
    if not isinstance(headers, Mapping):
        return None
    value = headers.get(_HEADER)
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _worker_id(task_id: str | None) -> str | None:
    if task_id is None:
        return None
    normalized = task_id.strip()
    return normalized if 0 < len(normalized) <= 255 else None


def _claim_version(task: _SignalTask | None) -> int | None:
    if task is None:
        return None
    try:
        version = task.request.operation_run_claim_version
    except AttributeError:
        return None
    return version if isinstance(version, int) and not isinstance(version, bool) else None
