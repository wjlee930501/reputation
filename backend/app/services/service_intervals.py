"""Transactional lifecycle for non-overlapping hospital service intervals."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monthly_control import HospitalServiceInterval


class ServiceIntervalProvenance(StrEnum):
    ACTIVATION = "ACTIVATION"
    RESUME = "RESUME"


async def open_service_interval(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    provenance: ServiceIntervalProvenance,
    *,
    occurred_at: datetime | None = None,
) -> HospitalServiceInterval:
    """Open one interval, reusing an existing open interval on a replay."""

    current = await db.scalar(
        select(HospitalServiceInterval)
        .where(
            HospitalServiceInterval.hospital_id == hospital_id,
            HospitalServiceInterval.ended_at.is_(None),
        )
        .with_for_update()
    )
    if current is not None:
        return current
    interval = HospitalServiceInterval(
        hospital_id=hospital_id,
        started_at=occurred_at or datetime.now(UTC),
        provenance=provenance.value,
    )
    db.add(interval)
    return interval


async def close_service_interval(
    db: AsyncSession, hospital_id: uuid.UUID, *, occurred_at: datetime | None = None
) -> HospitalServiceInterval | None:
    """Close exactly the current open interval, leaving provenance unchanged."""

    current = await db.scalar(
        select(HospitalServiceInterval)
        .where(
            HospitalServiceInterval.hospital_id == hospital_id,
            HospitalServiceInterval.ended_at.is_(None),
        )
        .with_for_update()
    )
    if current is None:
        return None
    current.ended_at = occurred_at or datetime.now(UTC)
    return current
