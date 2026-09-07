"""Resolve approved clinic writing standards for write and public-read gates.

``current`` is deliberately strict: every required text source must be processed
and the approved snapshot must match the complete current source set. It is the
only philosophy suitable for generation or publication.

``public_philosophy`` preserves already-published historical content while a new
source is being processed or approved, provided every source in the approved
baseline is still processed and unchanged. It must never be used for a write.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.essence import (
    PHOTO_SOURCE_TYPES,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
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
    complete_snapshot_is_fresh: bool | None = None

    @property
    def is_fresh(self) -> bool:
        return (
            self.complete_snapshot_is_fresh
            if self.complete_snapshot_is_fresh is not None
            else self.current is not None
        )

    @property
    def is_stale(self) -> bool:
        return self.approved is not None and not self.is_fresh

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
    source_asset_ids = getattr(approved, "source_asset_ids", None) if approved else None
    if approved and source_asset_ids:
        baseline_ids = {str(source_id) for source_id in source_asset_ids}
        baseline_processed = [
            source for source in processed_sources if str(source.id) in baseline_ids
        ]
        public_philosophy = (
            approved
            if compute_sources_snapshot_hash(baseline_processed) == approved.source_snapshot_hash
            else None
        )
    else:
        public_philosophy = approved if processed_snapshot_matches else None
    return EssenceReadiness(
        approved=approved,
        current=approved if fresh else None,
        public_philosophy=public_philosophy,
        processed_source_count=len(processed_sources),
        required_source_count=len(required_sources),
        current_snapshot_hash=snapshot,
        complete_snapshot_is_fresh=fresh,
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


async def get_current_approved_philosophy_id(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the strict current-snapshot approval id without source text.

    `get_essence_readiness()`와 동일한 신선도 규칙(§resolve_essence_readiness)을 쓰지만,
    소스 자산을 스냅샷 해시 계산에 필요한 4개 컬럼(id·content_hash·status·processed_at)만
    선택해 `raw_text`·`operator_note` 같은 대용량 컬럼을 읽지 않는다. 생성·수정·발행을
    허용하는 쓰기 게이트에서만 사용한다.
    """
    approved_id, readiness = await _get_lightweight_essence_readiness(db, hospital_id)
    return approved_id if readiness and readiness.current is not None else None


async def get_public_approved_philosophy_id(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return an intact historical approval id for read-only public serving.

    A new source may await automated processing and approval without hiding
    already-published content. The helper returns ``None`` as soon as a source in
    the approved baseline is excluded, unprocessed, or changed. It must never
    authorize generation, editing, or publication.
    """
    approved_id, readiness = await _get_lightweight_essence_readiness(db, hospital_id)
    return approved_id if readiness and readiness.public_philosophy is not None else None


async def _get_lightweight_essence_readiness(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> tuple[uuid.UUID | None, EssenceReadiness | None]:
    """Load only columns needed to evaluate current and historical read gates."""
    approved_row = (
        await db.execute(
            select(
                HospitalContentPhilosophy.id,
                HospitalContentPhilosophy.source_snapshot_hash,
                HospitalContentPhilosophy.source_asset_ids,
            ).where(
                HospitalContentPhilosophy.hospital_id == hospital_id,
                HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
            )
        )
    ).one_or_none()
    if approved_row is None:
        return None, None
    approved_id, source_snapshot_hash, source_asset_ids = approved_row

    sources_result = await db.execute(
        select(
            HospitalSourceAsset.id,
            HospitalSourceAsset.content_hash,
            HospitalSourceAsset.status,
            HospitalSourceAsset.processed_at,
        ).where(
            HospitalSourceAsset.hospital_id == hospital_id,
            HospitalSourceAsset.status != SourceStatus.EXCLUDED,
            HospitalSourceAsset.source_type.notin_(list(PHOTO_SOURCE_TYPES)),
        )
    )
    required_sources = [
        SimpleNamespace(
            id=row.id,
            content_hash=row.content_hash,
            status=row.status,
            processed_at=row.processed_at,
        )
        for row in sources_result.all()
    ]
    approved_stub = SimpleNamespace(
        source_snapshot_hash=source_snapshot_hash,
        source_asset_ids=source_asset_ids,
    )
    readiness = resolve_essence_readiness(approved_stub, required_sources)
    return approved_id, readiness
