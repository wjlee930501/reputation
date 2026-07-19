"""Resolve approved clinic writing standards for write and public-read gates.

Approval alone is insufficient: processed sources can change after approval.
Generation and publication require every source to be processed. Public reads
may keep serving content from the approved processed-source snapshot while a
new source is still pending, but must stop if that processed snapshot changes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PHOTO_SOURCE_TYPES,
    PhilosophyStatus,
    SourceStatus,
)
from app.services.essence_engine import compute_sources_snapshot_hash


@dataclass(frozen=True)
class EssenceReadiness:
    approved: HospitalContentPhilosophy | None
    current: HospitalContentPhilosophy | None
    processed_source_count: int
    required_source_count: int
    current_snapshot_hash: str
    public_philosophy: HospitalContentPhilosophy | None = None

    @property
    def is_fresh(self) -> bool:
        return self.current is not None

    @property
    def is_stale(self) -> bool:
        return self.approved is not None and self.current is None

    @property
    def has_unprocessed_sources(self) -> bool:
        return self.required_source_count != self.processed_source_count


def resolve_essence_readiness(
    approved: HospitalContentPhilosophy | None,
    required_sources: list[HospitalSourceAsset],
) -> EssenceReadiness:
    processed_sources = [
        source for source in required_sources if source.status == SourceStatus.PROCESSED
    ]
    snapshot = compute_sources_snapshot_hash(processed_sources)
    processed_snapshot_matches = bool(
        approved
        and processed_sources
        and approved.source_snapshot_hash
        and approved.source_snapshot_hash == snapshot
    )
    fresh = processed_snapshot_matches and len(processed_sources) == len(required_sources)
    return EssenceReadiness(
        approved=approved,
        current=approved if fresh else None,
        public_philosophy=approved if processed_snapshot_matches else None,
        processed_source_count=len(processed_sources),
        required_source_count=len(required_sources),
        current_snapshot_hash=snapshot,
    )


async def get_essence_readiness(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> EssenceReadiness:
    approved_result = await db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    )
    approved = approved_result.scalar_one_or_none()
    sources_result = await db.execute(
        select(HospitalSourceAsset).where(
            HospitalSourceAsset.hospital_id == hospital_id,
            HospitalSourceAsset.status != SourceStatus.EXCLUDED,
            HospitalSourceAsset.source_type.notin_(list(PHOTO_SOURCE_TYPES)),
        )
    )
    return resolve_essence_readiness(approved, list(sources_result.scalars().all()))


def get_essence_readiness_sync(db: Session, hospital_id: uuid.UUID) -> EssenceReadiness:
    approved = db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    ).scalar_one_or_none()
    required_sources = list(
        db.execute(
            select(HospitalSourceAsset).where(
                HospitalSourceAsset.hospital_id == hospital_id,
                HospitalSourceAsset.status != SourceStatus.EXCLUDED,
                HospitalSourceAsset.source_type.notin_(list(PHOTO_SOURCE_TYPES)),
            )
        )
        .scalars()
        .all()
    )
    return resolve_essence_readiness(approved, required_sources)


async def get_current_approved_philosophy(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> HospitalContentPhilosophy | None:
    return (await get_essence_readiness(db, hospital_id)).current


def get_current_approved_philosophy_sync(
    db: Session,
    hospital_id: uuid.UUID,
) -> HospitalContentPhilosophy | None:
    return get_essence_readiness_sync(db, hospital_id).current
