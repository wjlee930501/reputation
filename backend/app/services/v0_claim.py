"""V0 ANALYZING claim heartbeat — API and worker share one definition."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import OperationRun, OperationRunState
from app.models.sov import MeasurementRun

V0_CLAIM_MAX_AGE_SECONDS = 2400
_ACTIVE_RUN_STATES = (
    OperationRunState.REQUESTED,
    OperationRunState.QUEUED,
    OperationRunState.RUNNING,
)


def v0_claim_is_alive_sync(db, hospital_id: uuid.UUID) -> bool:
    """True when a recent RUNNING measurement still owns the ANALYZING claim."""
    cutoff = datetime.now(UTC) - timedelta(seconds=V0_CLAIM_MAX_AGE_SECONDS)
    running = db.execute(
        select(MeasurementRun.id)
        .where(
            MeasurementRun.hospital_id == hospital_id,
            MeasurementRun.status == "RUNNING",
            MeasurementRun.started_at.isnot(None),
            MeasurementRun.started_at >= cutoff,
        )
        .limit(1)
    )
    return running.scalar() is not None


async def v0_claim_is_alive(db: AsyncSession, hospital_id: uuid.UUID) -> bool:
    cutoff = datetime.now(UTC) - timedelta(seconds=V0_CLAIM_MAX_AGE_SECONDS)
    running = await db.scalar(
        select(MeasurementRun.id)
        .where(
            MeasurementRun.hospital_id == hospital_id,
            MeasurementRun.status == "RUNNING",
            MeasurementRun.started_at.isnot(None),
            MeasurementRun.started_at >= cutoff,
        )
        .limit(1)
    )
    return running is not None


async def latest_active_v0_run(db: AsyncSession, hospital_id: uuid.UUID) -> OperationRun | None:
    """In-flight TRIGGER_V0_REPORT run, if any. Used to 409 instead of double-enqueue."""
    return await db.scalar(
        select(OperationRun)
        .where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == "TRIGGER_V0_REPORT",
            OperationRun.state.in_(_ACTIVE_RUN_STATES),
        )
        .order_by(OperationRun.requested_at.desc())
        .limit(1)
    )
