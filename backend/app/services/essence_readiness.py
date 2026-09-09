"""Resolve the stable BaseEssence used by write and public-read gates.

An approval's source and evidence-noise hashes are immutable audit metadata. They
still describe whether today's evidence snapshot matches the approval-time input,
but ordinary ingestion drift never removes the BaseEssence from ``current`` or
``public_philosophy``. Only absence of an onboarded base closes those gates.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.essence import (
    AUTO_REVIEW_GAP_FIELD,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
)
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.essence_sources import required_text_source_predicate
from app.services.evidence_noise import (
    load_evidence_noise_hash,
    load_evidence_noise_hash_sync,
    load_evidence_noise_hashes,
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
    # Freshness remains diagnostic metadata only. It must not decide whether the
    # stable BaseEssence exists for generation/publication.
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
    return EssenceReadiness(
        approved=approved,
        current=approved,
        public_philosophy=approved,
        processed_source_count=len(processed_sources),
        required_source_count=len(required_sources),
        current_snapshot_hash=snapshot,
        complete_snapshot_is_fresh=fresh,
    )


def _base_candidate_predicate():
    """Base flag first, with APPROVED fallback for rolling-deploy compatibility."""

    return or_(
        HospitalContentPhilosophy.is_base.is_(True),
        HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
    )


def _base_candidate_ordering() -> tuple[Any, ...]:
    return (
        HospitalContentPhilosophy.is_base.desc(),
        HospitalContentPhilosophy.approved_at.desc().nullslast(),
        HospitalContentPhilosophy.version.desc(),
        HospitalContentPhilosophy.id.desc(),
    )


async def get_essence_readiness(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> EssenceReadiness:
    approved_result = await db.execute(
        select(HospitalContentPhilosophy)
        .where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            _base_candidate_predicate(),
        )
        .order_by(*_base_candidate_ordering())
        .limit(1)
    )
    approved = approved_result.scalar_one_or_none()
    sources_result = await db.execute(
        select(HospitalSourceAsset).where(
            HospitalSourceAsset.hospital_id == hospital_id,
            required_text_source_predicate(),
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
) -> HospitalContentPhilosophy | None:
    """Return the stable base for public serving without loading noise rows."""
    approved_result = await db.execute(
        select(HospitalContentPhilosophy)
        .where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            _base_candidate_predicate(),
        )
        .order_by(*_base_candidate_ordering())
        .limit(1)
    )
    approved = approved_result.scalar_one_or_none()
    sources_result = await db.execute(
        select(HospitalSourceAsset).where(
            HospitalSourceAsset.hospital_id == hospital_id,
            required_text_source_predicate(),
        )
    )
    return resolve_essence_readiness(
        approved, list(sources_result.scalars().all())
    ).public_philosophy


def get_essence_readiness_sync(db: Session, hospital_id: uuid.UUID) -> EssenceReadiness:
    approved = db.execute(
        select(HospitalContentPhilosophy)
        .where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            _base_candidate_predicate(),
        )
        .order_by(*_base_candidate_ordering())
        .limit(1)
    ).scalar_one_or_none()
    required_sources = list(
        db.execute(
            select(HospitalSourceAsset).where(
                HospitalSourceAsset.hospital_id == hospital_id,
                required_text_source_predicate(),
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
    """Return the stable BaseEssence id without source text.

    `get_essence_readiness()`와 동일한 base 규칙을 쓰지만,
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
    """Return the stable BaseEssence id for read-only public serving."""
    approved_id, readiness = await _get_lightweight_essence_readiness(
        db, hospital_id, include_noise=False
    )
    return _public_philosophy_id(approved_id, readiness)


async def get_public_approved_philosophy_ids(
    db: AsyncSession,
    hospital_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, uuid.UUID | None]:
    """Return the public-serving approval id per hospital in exactly two queries.

    병원 횡단 운영 큐가 병원마다 기준을 조회하면 화면 하나가 병원 수에 비례하는 쿼리를
    낸다. 승인 행과 필수 자료를 각각 한 번에 읽고 병원별로 묶어, 단건
    `get_public_approved_philosophy_id`와 같은 `_resolve_lightweight_readiness`로
    판정한다 — 조회 방식만 다르고 판정은 갈라질 수 없다.
    """
    ids = list(dict.fromkeys(hospital_ids))
    if not ids:
        return {}
    approved_rows = (
        await db.execute(
            select(
                HospitalContentPhilosophy.hospital_id,
                HospitalContentPhilosophy.id,
                HospitalContentPhilosophy.source_snapshot_hash,
                HospitalContentPhilosophy.source_asset_ids,
                HospitalContentPhilosophy.evidence_noise_hash,
                HospitalContentPhilosophy.is_base,
                HospitalContentPhilosophy.approved_at,
                HospitalContentPhilosophy.version,
            ).where(
                HospitalContentPhilosophy.hospital_id.in_(ids),
                _base_candidate_predicate(),
            )
            .order_by(*_base_candidate_ordering())
        )
    ).all()
    source_rows = (
        await db.execute(
            select(
                HospitalSourceAsset.hospital_id,
                HospitalSourceAsset.id,
                HospitalSourceAsset.content_hash,
                HospitalSourceAsset.status,
                HospitalSourceAsset.processed_at,
            ).where(
                HospitalSourceAsset.hospital_id.in_(ids),
                required_text_source_predicate(),
            )
        )
    ).all()
    # Flagged base rows sort ahead of rolling-deploy APPROVED fallbacks.
    approved_by_hospital = _group_base_candidates(approved_rows)
    sources_by_hospital: dict[uuid.UUID, list[Any]] = {}
    for row in source_rows:
        sources_by_hospital.setdefault(row.hospital_id, []).append(row)

    resolved: dict[uuid.UUID, uuid.UUID | None] = {}
    for hospital_id in ids:
        # 공개 판정은 `public_philosophy`만 보므로 노이즈 집합 조회가 결과를 바꾸지 않는다.
        resolved[hospital_id] = _public_philosophy_id(
            *_resolve_lightweight_readiness(
                approved_by_hospital.get(hospital_id),
                sources_by_hospital.get(hospital_id, ()),
                excluded_note_hash=None,
            )
        )
    return resolved


@dataclass(frozen=True, slots=True)
class EssenceReadinessState:
    """목록·헤더·현황이 공유하는 병원별 콘텐츠 준비 근거.

    `escalated_draft_findings`는 자동 검수가 남긴 보류 사유 전부다 — 현황 화면이 예외
    카드를 만들 때 초안을 다시 조회하지 않도록 여기서 함께 싣는다(같은 JSONB 필터가
    두 곳에 있으면 목록의 예외와 현황의 카드가 갈린다).

    `required_sources`는 자동 검수가 볼 수 있는 필수 자료 수다. 0이면 자동 검수는 시작조차
    하지 못하므로(WAITING_FOR_SOURCES) 남은 일은 시스템이 아니라 사람의 몫이다.
    """

    current: bool
    unprocessed_sources: int
    required_sources: int
    escalated_draft: bool
    escalated_draft_id: uuid.UUID | None = None
    escalated_draft_findings: tuple[str, ...] = ()


async def get_essence_readiness_states(
    db: AsyncSession,
    hospital_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, EssenceReadinessState]:
    """N개 병원의 콘텐츠 준비 상태를 상수 쿼리로 — 승인 행 · 필수 자료 · 노이즈 제외 집합 · 예외 초안.

    Snapshot/noise freshness remains diagnostic, so it is loaded here even though
    it can no longer clear ``current``. Base selection is shared with the single-
    hospital resolver.
    """
    ids = list(dict.fromkeys(hospital_ids))
    if not ids:
        return {}
    approved_rows = (
        await db.execute(
            select(
                HospitalContentPhilosophy.hospital_id,
                HospitalContentPhilosophy.id,
                HospitalContentPhilosophy.source_snapshot_hash,
                HospitalContentPhilosophy.source_asset_ids,
                HospitalContentPhilosophy.evidence_noise_hash,
                HospitalContentPhilosophy.is_base,
                HospitalContentPhilosophy.approved_at,
                HospitalContentPhilosophy.version,
            ).where(
                HospitalContentPhilosophy.hospital_id.in_(ids),
                _base_candidate_predicate(),
            )
            .order_by(*_base_candidate_ordering())
        )
    ).all()
    source_rows = (
        await db.execute(
            select(
                HospitalSourceAsset.hospital_id,
                HospitalSourceAsset.id,
                HospitalSourceAsset.content_hash,
                HospitalSourceAsset.status,
                HospitalSourceAsset.processed_at,
            ).where(
                HospitalSourceAsset.hospital_id.in_(ids),
                required_text_source_predicate(),
            )
        )
    ).all()
    noise_hashes = await load_evidence_noise_hashes(db, ids)

    approved_by_hospital = _group_base_candidates(approved_rows)
    sources_by_hospital: dict[uuid.UUID, list[Any]] = {}
    for row in source_rows:
        sources_by_hospital.setdefault(row.hospital_id, []).append(row)
    # 지금 자료 집합의 snapshot — 예외 초안을 이 판으로만 좁힌다. 승인 행이 없는 병원도
    # 자기 snapshot은 있으므로 자료 행에서 직접 센다.
    snapshot_hashes = {
        hospital_id: compute_sources_snapshot_hash(
            [
                row
                for row in sources_by_hospital.get(hospital_id, ())
                if row.status == SourceStatus.PROCESSED
            ]
        )
        for hospital_id in ids
    }
    escalated_drafts = await _load_escalated_drafts(db, ids, snapshot_hashes)

    resolved: dict[uuid.UUID, EssenceReadinessState] = {}
    for hospital_id in ids:
        sources = sources_by_hospital.get(hospital_id, [])
        _, readiness = _resolve_lightweight_readiness(
            approved_by_hospital.get(hospital_id),
            sources,
            excluded_note_hash=noise_hashes.get(hospital_id),
        )
        draft_id, findings = escalated_drafts.get(hospital_id, (None, ()))
        resolved[hospital_id] = EssenceReadinessState(
            current=readiness is not None and readiness.current is not None,
            # `resolve_essence_readiness`와 같은 정의(PROCESSED가 아닌 필수 자료). 승인 행이
            # 없는 병원도 남은 자료 수는 말할 수 있어야 하므로 자료 행에서 직접 센다.
            unprocessed_sources=sum(
                1 for row in sources if row.status != SourceStatus.PROCESSED
            ),
            required_sources=len(sources),
            escalated_draft=hospital_id in escalated_drafts,
            escalated_draft_id=draft_id,
            escalated_draft_findings=findings,
        )
    return resolved


def _group_base_candidates(rows: Iterable[Any]) -> dict[uuid.UUID, Any]:
    """Prefer a durable base flag over a rolling-deploy APPROVED fallback."""

    selected: dict[uuid.UUID, Any] = {}
    for row in rows:
        existing = selected.get(row.hospital_id)
        if existing is None or (
            bool(getattr(row, "is_base", False))
            and not bool(getattr(existing, "is_base", False))
        ):
            selected[row.hospital_id] = row
    return selected


async def _load_escalated_drafts(
    db: AsyncSession,
    hospital_ids: list[uuid.UUID],
    snapshot_hashes: dict[uuid.UUID, str],
) -> dict[uuid.UUID, tuple[uuid.UUID, tuple[str, ...]]]:
    """자동 검수가 보류한 DRAFT — 사람이 손대야 풀리는 예외와 그 보류 사유 전부.

    지금 자료 집합의 snapshot으로 만든 초안만 예외로 센다(쓰기 경로의
    `essence_auto_review._drafts_for_snapshot`와 같은 조건). 자료가 바뀌어 새 판이 자동
    승인된 뒤에도 옛 초안은 DRAFT로 남으므로, 판을 보지 않으면 그 병원은 영영 "예외 있음"이다.

    `unsupported_gaps`는 postgres에서만 JSONB인 variant라 컨테인먼트를 SQL로 강제하면 다른
    dialect에서 깨진다. 병원당 초안은 소수이므로 gap 목록만 읽어 파이썬에서 거른다.
    사유는 하나라도 빠지면 승인 게이트와 화면이 어긋나므로 전부 싣는다
    (`api/admin/essence.py`의 예외 승인 조건과 같은 목록).
    """
    rows = (
        await db.execute(
            select(
                HospitalContentPhilosophy.hospital_id,
                HospitalContentPhilosophy.id,
                HospitalContentPhilosophy.source_snapshot_hash,
                HospitalContentPhilosophy.unsupported_gaps,
            ).where(
                HospitalContentPhilosophy.hospital_id.in_(hospital_ids),
                HospitalContentPhilosophy.status == PhilosophyStatus.DRAFT,
            )
        )
    ).all()
    escalated: dict[uuid.UUID, tuple[uuid.UUID, tuple[str, ...]]] = {}
    for row in rows:
        if row.source_snapshot_hash != snapshot_hashes.get(row.hospital_id):
            continue
        gaps = [
            gap
            for gap in (row.unsupported_gaps or [])
            if isinstance(gap, dict) and gap.get("field") == AUTO_REVIEW_GAP_FIELD
        ]
        findings = tuple(str(gap["reason"]) for gap in gaps if gap.get("reason"))
        # 사유가 없으면 승인 게이트도 막지 않는다 — 목록의 예외 수와 현황의 카드가
        # 갈리지 않도록 여기서도 세지 않는다.
        if not findings:
            continue
        escalated.setdefault(row.hospital_id, (row.id, findings))
    return escalated


def _public_philosophy_id(
    approved_id: uuid.UUID | None,
    readiness: EssenceReadiness | None,
) -> uuid.UUID | None:
    """공개 읽기가 받아도 되는 stable BaseEssence id."""
    return approved_id if readiness and readiness.public_philosophy is not None else None


def _resolve_lightweight_readiness(
    approved_row: Any | None,
    source_rows: Iterable[Any],
    *,
    excluded_note_hash: str | None,
) -> tuple[uuid.UUID | None, EssenceReadiness | None]:
    """Evaluate one hospital's lightweight rows with the shared base rule.

    단건 조회와 묶음 조회가 각자 stub을 만들면 신선도 규칙이 갈라진다 — 판정은 여기뿐이다.
    """
    if approved_row is None:
        return None, None
    required_sources = [
        SimpleNamespace(
            id=row.id,
            content_hash=row.content_hash,
            status=row.status,
            processed_at=row.processed_at,
        )
        for row in source_rows
    ]
    approved_stub = SimpleNamespace(
        source_snapshot_hash=approved_row.source_snapshot_hash,
        source_asset_ids=approved_row.source_asset_ids,
        evidence_noise_hash=approved_row.evidence_noise_hash,
    )
    readiness = resolve_essence_readiness(
        approved_stub, required_sources, excluded_note_hash=excluded_note_hash
    )
    return approved_row.id, readiness


async def _get_lightweight_essence_readiness(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    *,
    include_noise: bool,
) -> tuple[uuid.UUID | None, EssenceReadiness | None]:
    """Load only columns needed to evaluate current and historical read gates.

    `include_noise=False` skips the evidence-noise query for public serving. Write
    callers include it so readiness can still report audit freshness accurately.
    """
    approved_row = (
        await db.execute(
            select(
                HospitalContentPhilosophy.id,
                HospitalContentPhilosophy.source_snapshot_hash,
                HospitalContentPhilosophy.source_asset_ids,
                HospitalContentPhilosophy.evidence_noise_hash,
                HospitalContentPhilosophy.is_base,
                HospitalContentPhilosophy.approved_at,
                HospitalContentPhilosophy.version,
            ).where(
                HospitalContentPhilosophy.hospital_id == hospital_id,
                _base_candidate_predicate(),
            )
            .order_by(*_base_candidate_ordering())
            .limit(1)
        )
    ).one_or_none()
    if approved_row is None:
        return None, None

    sources_result = await db.execute(
        select(
            HospitalSourceAsset.id,
            HospitalSourceAsset.content_hash,
            HospitalSourceAsset.status,
            HospitalSourceAsset.processed_at,
        ).where(
            HospitalSourceAsset.hospital_id == hospital_id,
            required_text_source_predicate(),
        )
    )
    return _resolve_lightweight_readiness(
        approved_row,
        sources_result.all(),
        excluded_note_hash=(
            await load_evidence_noise_hash(db, hospital_id) if include_noise else None
        ),
    )
