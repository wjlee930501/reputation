"""Admin API — hospital source-backed Content Essence."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital
from app.schemas.essence import (
    ApprovedPhilosophyResponse,
    PhilosophyApprove,
    PhilosophyDraftCreate,
    PhilosophyPatch,
    PhilosophyResponse,
    SourceAssetCreate,
    SourceAssetPatch,
    SourceAssetResponse,
)
from app.services.essence_engine import (
    compute_source_content_hash,
    process_source_asset,
    synthesize_philosophy,
    validate_philosophy_grounding,
    validate_source_excerpt,
)

router = APIRouter(prefix="/admin/hospitals/{hospital_id}/essence", tags=["Admin — Essence"])


@router.get("/sources", response_model=list[SourceAssetResponse])
async def list_sources(
    hospital_id: uuid.UUID,
    status_filter: SourceStatus | None = Query(default=None, alias="status"),
    source_type: SourceType | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    await _get_hospital_or_404(db, hospital_id)
    stmt = select(HospitalSourceAsset).where(HospitalSourceAsset.hospital_id == hospital_id)
    if status_filter:
        stmt = stmt.where(HospitalSourceAsset.status == status_filter)
    if source_type:
        stmt = stmt.where(HospitalSourceAsset.source_type == source_type)
    stmt = stmt.order_by(HospitalSourceAsset.created_at.desc())

    result = await db.execute(stmt)
    sources = result.scalars().all()
    counts = await _note_counts(db, [source.id for source in sources])
    return [_serialize_source(source, evidence_note_count=counts.get(source.id, 0)) for source in sources]


@router.post("/sources", status_code=status.HTTP_201_CREATED, response_model=SourceAssetResponse)
async def create_source(
    hospital_id: uuid.UUID,
    body: SourceAssetCreate,
    db: AsyncSession = Depends(get_db),
):
    await _get_hospital_or_404(db, hospital_id)
    source = HospitalSourceAsset(
        hospital_id=hospital_id,
        source_type=body.source_type,
        title=body.title,
        url=_clean_optional(body.url),
        raw_text=_clean_optional(body.raw_text),
        operator_note=_clean_optional(body.operator_note),
        source_metadata=body.source_metadata or {},
        content_hash=compute_source_content_hash(
            body.title,
            body.url,
            body.raw_text,
            body.operator_note,
        ),
        status=SourceStatus.PENDING,
        created_by=body.created_by,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return _serialize_source(source)


@router.get("/sources/{source_id}", response_model=SourceAssetResponse)
async def get_source(
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    source = await _get_source_or_404(db, hospital_id, source_id)
    notes = await _get_notes_for_source(db, source.id)
    return _serialize_source(source, evidence_notes=notes, evidence_note_count=len(notes))


@router.patch("/sources/{source_id}", response_model=SourceAssetResponse)
async def patch_source(
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
    body: SourceAssetPatch,
    db: AsyncSession = Depends(get_db),
):
    source = await _get_source_or_404(db, hospital_id, source_id)
    update = body.model_dump(exclude_unset=True)
    material_fields = {"source_type", "title", "url", "raw_text", "operator_note", "source_metadata"}
    material_changed = bool(material_fields.intersection(update.keys()))

    for field_name, value in update.items():
        if field_name in {"url", "raw_text", "operator_note"}:
            value = _clean_optional(value)
        setattr(source, field_name, value)

    if not ((source.url and source.url.strip()) or (source.raw_text and source.raw_text.strip())):
        raise HTTPException(status_code=400, detail="url 또는 raw_text 중 하나는 필수입니다.")

    if material_changed:
        await db.execute(
            delete(HospitalSourceEvidenceNote).where(
                HospitalSourceEvidenceNote.source_asset_id == source.id
            )
        )
        source.status = SourceStatus.PENDING
        source.process_error = None
        source.processed_at = None
        source.content_hash = compute_source_content_hash(
            source.title,
            source.url,
            source.raw_text,
            source.operator_note,
        )

    await db.commit()
    await db.refresh(source)
    return _serialize_source(source)


@router.post("/sources/{source_id}/process", response_model=SourceAssetResponse)
async def process_source(
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    source = await _get_source_or_404(db, hospital_id, source_id)
    if source.status == SourceStatus.EXCLUDED:
        raise HTTPException(status_code=400, detail="Excluded source는 처리할 수 없습니다.")
    if not source.raw_text or not source.raw_text.strip():
        raise HTTPException(status_code=400, detail="raw_text가 없는 URL-only source는 처리할 수 없습니다.")

    try:
        payloads = process_source_asset(source)
        for payload in payloads:
            if not validate_source_excerpt(source, payload.source_excerpt):
                raise ValueError(f"source_excerpt가 원문에 존재하지 않습니다: {payload.source_excerpt[:80]}")

        await db.execute(
            delete(HospitalSourceEvidenceNote).where(
                HospitalSourceEvidenceNote.source_asset_id == source.id
            )
        )
        notes = [
            HospitalSourceEvidenceNote(
                hospital_id=hospital_id,
                source_asset_id=source.id,
                note_type=payload.note_type,
                claim=payload.claim,
                source_excerpt=payload.source_excerpt,
                excerpt_start=payload.excerpt_start,
                excerpt_end=payload.excerpt_end,
                confidence=payload.confidence,
                note_metadata=payload.note_metadata,
            )
            for payload in payloads
        ]
        db.add_all(notes)
        source.status = SourceStatus.PROCESSED
        source.process_error = None
        source.processed_at = datetime.now(timezone.utc)
        source.content_hash = compute_source_content_hash(
            source.title,
            source.url,
            source.raw_text,
            source.operator_note,
        )
        await db.commit()
        await db.refresh(source)
        return _serialize_source(source, evidence_notes=notes, evidence_note_count=len(notes))
    except ValueError as exc:
        source.status = SourceStatus.ERROR
        source.process_error = str(exc)
        await db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sources/{source_id}/exclude", response_model=SourceAssetResponse)
async def exclude_source(
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    source = await _get_source_or_404(db, hospital_id, source_id)
    source.status = SourceStatus.EXCLUDED
    await db.commit()
    await db.refresh(source)
    notes = await _get_notes_for_source(db, source.id)
    return _serialize_source(source, evidence_notes=notes, evidence_note_count=len(notes))


@router.get("/philosophies", response_model=list[PhilosophyResponse])
async def list_philosophies(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await _get_hospital_or_404(db, hospital_id)
    result = await db.execute(
        select(HospitalContentPhilosophy)
        .where(HospitalContentPhilosophy.hospital_id == hospital_id)
        .order_by(HospitalContentPhilosophy.version.desc())
    )
    return [_serialize_philosophy(item) for item in result.scalars().all()]


@router.get("/philosophy/approved", response_model=ApprovedPhilosophyResponse)
async def get_approved_philosophy(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await _get_hospital_or_404(db, hospital_id)
    approved = await _get_approved(db, hospital_id)
    return {"approved": _serialize_philosophy(approved) if approved else None}


@router.post("/philosophy/draft", status_code=status.HTTP_201_CREATED, response_model=PhilosophyResponse)
async def create_philosophy_draft(
    hospital_id: uuid.UUID,
    body: PhilosophyDraftCreate,
    db: AsyncSession = Depends(get_db),
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    sources = await _select_processed_sources(db, hospital_id, body.source_asset_ids)
    if not sources:
        raise HTTPException(status_code=400, detail="처리된 source가 1개 이상 필요합니다.")

    notes = await _get_notes_for_sources(db, [source.id for source in sources])
    if not notes:
        raise HTTPException(status_code=400, detail="철학 초안 생성에 사용할 evidence note가 없습니다.")

    payload = synthesize_philosophy(hospital, sources, notes, operator_note=body.operator_note)
    grounding_errors = validate_philosophy_grounding(payload, notes)
    if grounding_errors:
        raise HTTPException(status_code=422, detail={"grounding_errors": grounding_errors})

    version = await _next_version(db, hospital_id)
    philosophy = HospitalContentPhilosophy(
        hospital_id=hospital_id,
        version=version,
        status=PhilosophyStatus.DRAFT,
        created_by=body.created_by,
        **payload,
    )
    db.add(philosophy)
    await db.commit()
    await db.refresh(philosophy)
    return _serialize_philosophy(philosophy)


@router.patch("/philosophy/{philosophy_id}", response_model=PhilosophyResponse)
async def patch_philosophy(
    hospital_id: uuid.UUID,
    philosophy_id: uuid.UUID,
    body: PhilosophyPatch,
    db: AsyncSession = Depends(get_db),
):
    philosophy = await _get_philosophy_or_404(db, hospital_id, philosophy_id)
    if philosophy.status != PhilosophyStatus.DRAFT:
        raise HTTPException(status_code=400, detail="APPROVED/ARCHIVED philosophy는 직접 수정할 수 없습니다.")

    update = body.model_dump(exclude_unset=True)
    for field_name, value in update.items():
        setattr(philosophy, field_name, value)

    if "evidence_map" in update:
        notes = await _get_notes_for_philosophy(db, philosophy)
        grounding_errors = validate_philosophy_grounding(philosophy, notes)
        if grounding_errors:
            raise HTTPException(status_code=422, detail={"grounding_errors": grounding_errors})

    await db.commit()
    await db.refresh(philosophy)
    return _serialize_philosophy(philosophy)


@router.post("/philosophy/{philosophy_id}/approve", response_model=PhilosophyResponse)
async def approve_philosophy(
    hospital_id: uuid.UUID,
    philosophy_id: uuid.UUID,
    body: PhilosophyApprove,
    db: AsyncSession = Depends(get_db),
):
    philosophy = await _get_philosophy_or_404(db, hospital_id, philosophy_id)
    if philosophy.status != PhilosophyStatus.DRAFT:
        raise HTTPException(status_code=400, detail="DRAFT philosophy만 승인할 수 있습니다.")

    notes = await _get_notes_for_philosophy(db, philosophy)
    grounding_errors = validate_philosophy_grounding(philosophy, notes)
    if grounding_errors:
        raise HTTPException(status_code=422, detail={"grounding_errors": grounding_errors})

    previous_result = await db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    )
    for previous in previous_result.scalars().all():
        if previous.id != philosophy.id:
            previous.status = PhilosophyStatus.ARCHIVED
    await db.flush()

    philosophy.status = PhilosophyStatus.APPROVED
    philosophy.reviewed_by = body.reviewed_by
    philosophy.approval_note = body.approval_note
    philosophy.approved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(philosophy)
    return _serialize_philosophy(philosophy)


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


async def _get_source_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
) -> HospitalSourceAsset:
    source = await db.get(HospitalSourceAsset, source_id)
    if not source or source.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


async def _get_philosophy_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    philosophy_id: uuid.UUID,
) -> HospitalContentPhilosophy:
    philosophy = await db.get(HospitalContentPhilosophy, philosophy_id)
    if not philosophy or philosophy.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Philosophy not found")
    return philosophy


async def _get_approved(db: AsyncSession, hospital_id: uuid.UUID) -> HospitalContentPhilosophy | None:
    result = await db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    )
    return result.scalar_one_or_none()


async def _note_counts(db: AsyncSession, source_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not source_ids:
        return {}
    result = await db.execute(
        select(HospitalSourceEvidenceNote.source_asset_id, func.count())
        .where(HospitalSourceEvidenceNote.source_asset_id.in_(source_ids))
        .group_by(HospitalSourceEvidenceNote.source_asset_id)
    )
    return {source_id: int(count) for source_id, count in result.all()}


async def _get_notes_for_source(
    db: AsyncSession,
    source_id: uuid.UUID,
) -> list[HospitalSourceEvidenceNote]:
    result = await db.execute(
        select(HospitalSourceEvidenceNote)
        .where(HospitalSourceEvidenceNote.source_asset_id == source_id)
        .order_by(HospitalSourceEvidenceNote.created_at.asc())
    )
    return result.scalars().all()


async def _get_notes_for_sources(
    db: AsyncSession,
    source_ids: list[uuid.UUID],
) -> list[HospitalSourceEvidenceNote]:
    result = await db.execute(
        select(HospitalSourceEvidenceNote)
        .where(HospitalSourceEvidenceNote.source_asset_id.in_(source_ids))
        .order_by(HospitalSourceEvidenceNote.created_at.asc())
    )
    return result.scalars().all()


async def _get_notes_for_philosophy(
    db: AsyncSession,
    philosophy: HospitalContentPhilosophy,
) -> list[HospitalSourceEvidenceNote]:
    source_ids = [uuid.UUID(str(source_id)) for source_id in (philosophy.source_asset_ids or [])]
    if not source_ids:
        result = await db.execute(
            select(HospitalSourceEvidenceNote).where(
                HospitalSourceEvidenceNote.hospital_id == philosophy.hospital_id
            )
        )
        return result.scalars().all()
    return await _get_notes_for_sources(db, source_ids)


async def _select_processed_sources(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    source_asset_ids: list[str] | None,
) -> list[HospitalSourceAsset]:
    stmt = select(HospitalSourceAsset).where(
        HospitalSourceAsset.hospital_id == hospital_id,
        HospitalSourceAsset.status == SourceStatus.PROCESSED,
    )
    if source_asset_ids:
        try:
            ids = [uuid.UUID(str(item)) for item in source_asset_ids]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="source_asset_ids 형식이 올바르지 않습니다.") from exc
        stmt = stmt.where(HospitalSourceAsset.id.in_(ids))
    stmt = stmt.order_by(HospitalSourceAsset.processed_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


async def _next_version(db: AsyncSession, hospital_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.max(HospitalContentPhilosophy.version)).where(
            HospitalContentPhilosophy.hospital_id == hospital_id
        )
    )
    return int(result.scalar_one() or 0) + 1


def _serialize_source(
    source: HospitalSourceAsset,
    evidence_note_count: int = 0,
    evidence_notes: list[HospitalSourceEvidenceNote] | None = None,
) -> dict:
    return {
        "id": str(source.id),
        "hospital_id": str(source.hospital_id),
        "source_type": source.source_type,
        "title": source.title,
        "url": source.url,
        "raw_text": source.raw_text,
        "operator_note": source.operator_note,
        "source_metadata": source.source_metadata or {},
        "content_hash": source.content_hash,
        "status": source.status,
        "process_error": source.process_error,
        "processed_at": source.processed_at.isoformat() if source.processed_at else None,
        "created_by": source.created_by,
        "updated_by": source.updated_by,
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "updated_at": source.updated_at.isoformat() if source.updated_at else None,
        "evidence_note_count": evidence_note_count,
        "evidence_notes": [_serialize_note(note) for note in evidence_notes] if evidence_notes is not None else None,
    }


def _serialize_note(note: HospitalSourceEvidenceNote) -> dict:
    return {
        "id": str(note.id),
        "hospital_id": str(note.hospital_id),
        "source_asset_id": str(note.source_asset_id),
        "note_type": note.note_type,
        "claim": note.claim,
        "source_excerpt": note.source_excerpt,
        "excerpt_start": note.excerpt_start,
        "excerpt_end": note.excerpt_end,
        "confidence": note.confidence,
        "note_metadata": note.note_metadata or {},
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


def _serialize_philosophy(philosophy: HospitalContentPhilosophy) -> dict:
    return {
        "id": str(philosophy.id),
        "hospital_id": str(philosophy.hospital_id),
        "version": philosophy.version,
        "status": philosophy.status,
        "positioning_statement": philosophy.positioning_statement,
        "doctor_voice": philosophy.doctor_voice,
        "patient_promise": philosophy.patient_promise,
        "content_principles": philosophy.content_principles or [],
        "tone_guidelines": philosophy.tone_guidelines or [],
        "must_use_messages": philosophy.must_use_messages or [],
        "avoid_messages": philosophy.avoid_messages or [],
        "treatment_narratives": philosophy.treatment_narratives or [],
        "local_context": philosophy.local_context or {},
        "medical_ad_risk_rules": philosophy.medical_ad_risk_rules or [],
        "evidence_map": philosophy.evidence_map or {},
        "source_asset_ids": philosophy.source_asset_ids or [],
        "unsupported_gaps": philosophy.unsupported_gaps or [],
        "conflict_notes": philosophy.conflict_notes or [],
        "synthesis_notes": philosophy.synthesis_notes,
        "source_snapshot_hash": philosophy.source_snapshot_hash,
        "created_by": philosophy.created_by,
        "reviewed_by": philosophy.reviewed_by,
        "approved_at": philosophy.approved_at.isoformat() if philosophy.approved_at else None,
        "approval_note": philosophy.approval_note,
        "created_at": philosophy.created_at.isoformat() if philosophy.created_at else None,
        "updated_at": philosophy.updated_at.isoformat() if philosophy.updated_at else None,
    }


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
