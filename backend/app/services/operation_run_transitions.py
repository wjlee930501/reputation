"""Lease ownership and terminal compare-and-swap transitions for OperationRun."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import JSONValue, OperationRun, OperationRunState

_TERMINAL_STATES = frozenset(
    {
        OperationRunState.SUCCEEDED,
        OperationRunState.PARTIAL,
        OperationRunState.FAILED,
        OperationRunState.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class LeaseClaim:
    run_id: uuid.UUID
    worker_id: str
    lease_seconds: int
    claimed_at: datetime


@dataclass(frozen=True, slots=True)
class Heartbeat:
    run_id: uuid.UUID
    worker_id: str
    expected_version: int
    lease_seconds: int
    heartbeat_at: datetime


@dataclass(frozen=True, slots=True)
class TerminalTransition:
    run_id: uuid.UUID
    worker_id: str
    expected_version: int
    state: OperationRunState
    completed_at: datetime
    total_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    result_summary: dict[str, JSONValue] | None = None
    safe_error_code: str | None = None
    safe_error_message: str | None = None


@dataclass(frozen=True, slots=True)
class TerminalResult:
    run: OperationRun
    changed: bool


@dataclass(frozen=True, slots=True)
class OperationTransitionRejected(Exception):
    run_id: uuid.UUID
    state: str

    def __str__(self) -> str:
        return f"operation run {self.run_id} rejects transition from {self.state}"


async def mark_operation_queued(
    db: AsyncSession, run_id: uuid.UUID, accepted_at: datetime
) -> OperationRun:
    """Record broker acceptance unless the worker already advanced the run."""
    statement = (
        update(OperationRun)
        .where(
            OperationRun.id == run_id,
            OperationRun.state == OperationRunState.REQUESTED,
        )
        .values(
            state=OperationRunState.QUEUED,
            queued_at=accepted_at,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun)
    )
    accepted = (await db.execute(statement)).scalar_one_or_none()
    if accepted is not None:
        return accepted
    current = await db.scalar(
        select(OperationRun)
        .where(OperationRun.id == run_id)
        .execution_options(populate_existing=True)
    )
    if current is None:
        raise OperationTransitionRejected(run_id, "MISSING")
    return current


async def claim_operation_run(db: AsyncSession, claim: LeaseClaim) -> OperationRun | None:
    """Claim a queued run or reclaim a RUNNING run whose lease expired."""
    expires_at = claim.claimed_at + timedelta(seconds=claim.lease_seconds)
    statement = (
        update(OperationRun)
        .where(
            OperationRun.id == claim.run_id,
            or_(
                OperationRun.state == OperationRunState.QUEUED,
                and_(
                    OperationRun.state == OperationRunState.RUNNING,
                    OperationRun.lease_expires_at <= claim.claimed_at,
                ),
            ),
        )
        .values(
            state=OperationRunState.RUNNING,
            started_at=func.coalesce(OperationRun.started_at, claim.claimed_at),
            heartbeat_at=claim.claimed_at,
            lease_owner=claim.worker_id[:255],
            lease_expires_at=expires_at,
            attempt_count=OperationRun.attempt_count + 1,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun)
    )
    run = (await db.execute(statement)).scalar_one_or_none()
    await db.commit()
    return run


async def heartbeat_operation_run(db: AsyncSession, heartbeat: Heartbeat) -> OperationRun | None:
    """Extend one live lease only when its owner and version still match."""
    statement = (
        update(OperationRun)
        .where(
            OperationRun.id == heartbeat.run_id,
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.lease_owner == heartbeat.worker_id,
            OperationRun.version == heartbeat.expected_version,
            OperationRun.lease_expires_at >= heartbeat.heartbeat_at,
        )
        .values(
            heartbeat_at=heartbeat.heartbeat_at,
            lease_expires_at=heartbeat.heartbeat_at
            + timedelta(seconds=heartbeat.lease_seconds),
            version=OperationRun.version + 1,
        )
        .returning(OperationRun)
    )
    run = (await db.execute(statement)).scalar_one_or_none()
    await db.commit()
    return run


async def finish_operation_run(
    db: AsyncSession, transition: TerminalTransition
) -> TerminalResult:
    """Apply a terminal result once; stale workers cannot overwrite newer truth."""
    if transition.state not in _TERMINAL_STATES:
        raise OperationTransitionRejected(transition.run_id, transition.state)
    statement = (
        update(OperationRun)
        .where(
            OperationRun.id == transition.run_id,
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.lease_owner == transition.worker_id,
            OperationRun.version == transition.expected_version,
        )
        .values(
            state=transition.state,
            completed_at=transition.completed_at,
            lease_owner=None,
            lease_expires_at=None,
            total_count=transition.total_count,
            success_count=transition.success_count,
            failure_count=transition.failure_count,
            skipped_count=transition.skipped_count,
            result_summary=transition.result_summary,
            safe_error_code=transition.safe_error_code,
            safe_error_message=transition.safe_error_message,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun)
    )
    changed = (await db.execute(statement)).scalar_one_or_none()
    if changed is not None:
        await db.commit()
        return TerminalResult(run=changed, changed=True)
    current = await db.get(OperationRun, transition.run_id)
    if current is None:
        raise OperationTransitionRejected(transition.run_id, "MISSING")
    return TerminalResult(run=current, changed=False)


def is_terminal_state(state: str) -> bool:
    return state in _TERMINAL_STATES
