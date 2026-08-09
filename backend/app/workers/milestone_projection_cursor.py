"""Durable state cursor for lossless milestone transition projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import OperationRun, OperationRunState
from app.workers.milestone_projection_support import ProjectionWindow

_OPERATION_TYPE: Final = "MILESTONE_PROJECTION"


@dataclass(frozen=True, slots=True)
class ProjectionCursor:
    previous_states: dict[str, str]
    delivery_since: datetime
    replayed: bool


async def load_projection_cursor(db: AsyncSession, window: ProjectionWindow) -> ProjectionCursor:
    key = _window_key(window)
    current = await db.scalar(
        select(OperationRun.id).where(
            OperationRun.operation_type == _OPERATION_TYPE,
            OperationRun.idempotency_key == key,
        )
    )
    if current is not None:
        return ProjectionCursor({}, window.start, True)
    previous = await db.scalar(
        select(OperationRun)
        .where(
            OperationRun.operation_type == _OPERATION_TYPE,
            OperationRun.state == OperationRunState.SUCCEEDED.value,
            OperationRun.completed_at < window.end,
        )
        .order_by(OperationRun.completed_at.desc())
        .limit(1)
    )
    if previous is None:
        return ProjectionCursor({}, window.start, False)
    completed_at = previous.completed_at or window.start
    return ProjectionCursor(_stored_states(previous), completed_at, False)


async def record_projection_cursor(
    db: AsyncSession,
    window: ProjectionWindow,
    states: dict[str, str],
    milestone_count: int,
) -> None:
    statement = (
        insert(OperationRun)
        .values(
            operation_type=_OPERATION_TYPE,
            state=OperationRunState.SUCCEEDED.value,
            idempotency_key=_window_key(window),
            attempt_count=1,
            total_count=milestone_count,
            success_count=milestone_count,
            failure_count=0,
            skipped_count=0,
            request_payload={
                "window_start": window.start.isoformat(),
                "window_end": window.end.isoformat(),
            },
            result_summary={"states": states},
            requested_at=window.end,
            started_at=window.end,
            completed_at=window.end,
            created_at=window.end,
            updated_at=window.end,
        )
        .on_conflict_do_nothing()
    )
    await db.execute(statement)


def _stored_states(run: OperationRun) -> dict[str, str]:
    summary = run.result_summary
    stored = summary.get("states") if isinstance(summary, dict) else None
    if not isinstance(stored, dict):
        return {}
    return {
        key: value
        for key, value in stored.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _window_key(window: ProjectionWindow) -> str:
    return f"milestone-window:{window.start.isoformat()}:{window.end.isoformat()}"
