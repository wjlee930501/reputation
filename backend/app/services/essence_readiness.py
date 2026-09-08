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
from app.services.evidence_noise import (
    load_evidence_noise_hash,
    load_evidence_noise_hash_sync,
)


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
    *,
    excluded_note_hash: str | None = None,
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
    # 노이즈 제외 집합은 엄격한 current에만 관여한다. 컬럼 이전 승인(NULL)이거나 호출자가
    # 현재 집합을 계산하지 않았으면(None) 이 조건은 통과시킨다. public_philosophy는 자료
    # snapshot만 보므로 노트 하나를 숨겨도 공개 글이 사라지지 않는다.
    approved_noise_hash = getattr(approved, "evidence_noise_hash", None) if approved else None
    noise_matches = (
        approved_noise_hash is None
        or excluded_note_hash is None
        or approved_noise_hash == excluded_note_hash
    )
    fresh = (
        processed_snapshot_matches
        and len(processed_sources) == len(required_sources)
        and noise_matches
    )
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
    return resolve_essence_readiness(
        approved,
        list(sources_result.scalars().all()),
        excluded_note_hash=await load_evidence_noise_hash(db, hospital_id),
    )


async def get_public_essence_readiness(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> EssenceReadiness:
    """공개 읽기 전용: `public_philosophy`만 필요하므로 노이즈 집합을 조회하지 않는다.

    엄격한 `current`는 이 결과에서 읽지 않는다 — 노이즈 hash를 넘기지 않아 관대하게 계산되므로
    생성/발행 게이트에 쓰면 H-02가 다시 열린다.
    """
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
    return resolve_essence_readiness(
        approved,
        required_sources,
        excluded_note_hash=load_evidence_noise_hash_sync(db, hospital_id),
    )


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
    approved_id, readiness = await _get_lightweight_essence_readiness(
        db, hospital_id, include_noise=True
    )
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
    approved_id, readiness = await _get_lightweight_essence_readiness(
        db, hospital_id, include_noise=False
    )
    return approved_id if readiness and readiness.public_philosophy is not None else None


async def _get_lightweight_essence_readiness(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    *,
    include_noise: bool,
) -> tuple[uuid.UUID | None, EssenceReadiness | None]:
    """Load only columns needed to evaluate current and historical read gates.

    `include_noise=False`는 노이즈 집합 조회를 건너뛴다 — 공개 읽기 경로는
    `public_philosophy`만 보므로 그 쿼리가 결과를 바꾸지 않는다. 엄격한 `current`를
    읽는 호출자는 반드시 `include_noise=True`로 불러야 한다.
    """
    approved_row = (
        await db.execute(
            select(
                HospitalContentPhilosophy.id,
                HospitalContentPhilosophy.source_snapshot_hash,
                HospitalContentPhilosophy.source_asset_ids,
                HospitalContentPhilosophy.evidence_noise_hash,
            ).where(
                HospitalContentPhilosophy.hospital_id == hospital_id,
                HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
            )
        )
    ).one_or_none()
    if approved_row is None:
        return None, None
    approved_id, source_snapshot_hash, source_asset_ids, evidence_noise_hash = approved_row

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
        evidence_noise_hash=evidence_noise_hash,
    )
    readiness = resolve_essence_readiness(
        approved_stub,
        required_sources,
        excluded_note_hash=(
            await load_evidence_noise_hash(db, hospital_id) if include_noise else None
        ),
    )
    return approved_id, readiness
