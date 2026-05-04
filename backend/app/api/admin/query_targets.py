"""
Admin API — patient-question strategy
GET    /admin/hospitals/{id}/query-targets
POST   /admin/hospitals/{id}/query-targets
GET    /admin/hospitals/{id}/query-targets/{target_id}
PATCH  /admin/hospitals/{id}/query-targets/{target_id}
DELETE /admin/hospitals/{id}/query-targets/{target_id}
POST   /admin/hospitals/{id}/query-targets/{target_id}/variants
PATCH  /admin/hospitals/{id}/query-targets/{target_id}/variants/{variant_id}
DELETE /admin/hospitals/{id}/query-targets/{target_id}/variants/{variant_id}
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.sov import AIQueryTarget, AIQueryVariant, QueryMatrix
from app.schemas.query_target import (
    AIQueryTargetCreate,
    AIQueryTargetDetail,
    AIQueryTargetListItem,
    AIQueryTargetUpdate,
    AIQueryVariantCreate,
    AIQueryVariantResponse,
    AIQueryVariantUpdate,
)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Patient Questions"])

ARCHIVED = "ARCHIVED"


@router.get("/{hospital_id}/query-targets", response_model=list[AIQueryTargetListItem])
async def list_query_targets(
    hospital_id: uuid.UUID,
    include_archived: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
):
    """List strategy-level patient questions for a hospital."""
    await _get_hospital_or_404(db, hospital_id)

    stmt = (
        select(AIQueryTarget)
        .options(selectinload(AIQueryTarget.variants))
        .where(AIQueryTarget.hospital_id == hospital_id)
    )
    if not include_archived:
        stmt = stmt.where(AIQueryTarget.status != ARCHIVED)

    result = await db.execute(stmt)
    targets = result.scalars().all()
    targets = sorted(targets, key=_target_sort_key)
    return [_serialize_target(target) for target in targets]


@router.post(
    "/{hospital_id}/query-targets",
    status_code=status.HTTP_201_CREATED,
    response_model=AIQueryTargetDetail,
)
async def create_query_target(
    hospital_id: uuid.UUID,
    body: AIQueryTargetCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a strategy-level patient question and optional initial variants."""
    await _get_hospital_or_404(db, hospital_id)

    target_data = body.model_dump(exclude={"variants"})
    target = AIQueryTarget(hospital_id=hospital_id, **target_data)
    db.add(target)
    await db.flush()

    seen_variants: set[tuple[str, str, str]] = set()
    for variant_body in body.variants:
        await _validate_query_matrix(db, hospital_id, variant_body.query_matrix_id)
        variant_key = _variant_key(variant_body.query_text, variant_body.platform, variant_body.language)
        if variant_key in seen_variants:
            continue
        seen_variants.add(variant_key)
        db.add(
            AIQueryVariant(
                query_target_id=target.id,
                **variant_body.model_dump(),
            )
        )

    await db.commit()
    target = await _get_target_or_404(db, hospital_id, target.id)
    return _serialize_target(target)


@router.get("/{hospital_id}/query-targets/{target_id}", response_model=AIQueryTargetDetail)
async def get_query_target(
    hospital_id: uuid.UUID,
    target_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    target = await _get_target_or_404(db, hospital_id, target_id)
    return _serialize_target(target)


@router.patch("/{hospital_id}/query-targets/{target_id}", response_model=AIQueryTargetDetail)
async def update_query_target(
    hospital_id: uuid.UUID,
    target_id: uuid.UUID,
    body: AIQueryTargetUpdate,
    db: AsyncSession = Depends(get_db),
):
    target = await _get_target_or_404(db, hospital_id, target_id)
    update_data = body.model_dump(exclude_unset=True)
    _apply_target_update(target, update_data)

    await db.commit()
    target = await _get_target_or_404(db, hospital_id, target_id)
    return _serialize_target(target)


@router.delete("/{hospital_id}/query-targets/{target_id}", response_model=AIQueryTargetDetail)
async def archive_query_target(
    hospital_id: uuid.UUID,
    target_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Archive instead of hard-deleting so monthly strategy history is preserved."""
    target = await _get_target_or_404(db, hospital_id, target_id)
    target.status = ARCHIVED
    await db.commit()
    target = await _get_target_or_404(db, hospital_id, target_id)
    return _serialize_target(target)


@router.post(
    "/{hospital_id}/query-targets/{target_id}/variants",
    status_code=status.HTTP_201_CREATED,
    response_model=AIQueryVariantResponse,
)
async def add_query_variant(
    hospital_id: uuid.UUID,
    target_id: uuid.UUID,
    body: AIQueryVariantCreate,
    db: AsyncSession = Depends(get_db),
):
    target = await _get_target_or_404(db, hospital_id, target_id)
    await _validate_query_matrix(db, hospital_id, body.query_matrix_id)

    duplicate = await _find_existing_variant(db, target.id, body.query_text, body.platform, body.language)
    if duplicate:
        if not duplicate.is_active:
            duplicate.is_active = True
            await db.commit()
            await db.refresh(duplicate)
        return _serialize_variant(duplicate)

    variant = AIQueryVariant(query_target_id=target.id, **body.model_dump())
    db.add(variant)
    await db.commit()
    await db.refresh(variant)
    return _serialize_variant(variant)


@router.patch(
    "/{hospital_id}/query-targets/{target_id}/variants/{variant_id}",
    response_model=AIQueryVariantResponse,
)
async def update_query_variant(
    hospital_id: uuid.UUID,
    target_id: uuid.UUID,
    variant_id: uuid.UUID,
    body: AIQueryVariantUpdate,
    db: AsyncSession = Depends(get_db),
):
    variant = await _get_variant_or_404(db, hospital_id, target_id, variant_id)
    update_data = body.model_dump(exclude_unset=True)
    if "query_matrix_id" in update_data:
        await _validate_query_matrix(db, hospital_id, update_data["query_matrix_id"])
    for field, value in update_data.items():
        setattr(variant, field, value)

    await db.commit()
    await db.refresh(variant)
    return _serialize_variant(variant)


@router.delete(
    "/{hospital_id}/query-targets/{target_id}/variants/{variant_id}",
    response_model=AIQueryVariantResponse,
)
async def deactivate_query_variant(
    hospital_id: uuid.UUID,
    target_id: uuid.UUID,
    variant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Deactivate variant instead of deleting execution history links."""
    variant = await _get_variant_or_404(db, hospital_id, target_id, variant_id)
    variant.is_active = False
    await db.commit()
    await db.refresh(variant)
    return _serialize_variant(variant)


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


async def _get_target_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    target_id: uuid.UUID,
) -> AIQueryTarget:
    result = await db.execute(
        select(AIQueryTarget)
        .options(selectinload(AIQueryTarget.variants))
        .where(
            AIQueryTarget.id == target_id,
            AIQueryTarget.hospital_id == hospital_id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Patient question not found")
    return target


async def _get_variant_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    target_id: uuid.UUID,
    variant_id: uuid.UUID,
) -> AIQueryVariant:
    result = await db.execute(
        select(AIQueryVariant)
        .join(AIQueryTarget)
        .where(
            AIQueryVariant.id == variant_id,
            AIQueryVariant.query_target_id == target_id,
            AIQueryTarget.id == target_id,
            AIQueryTarget.hospital_id == hospital_id,
        )
    )
    variant = result.scalar_one_or_none()
    if not variant:
        raise HTTPException(status_code=404, detail="Query variant not found")
    return variant


async def _validate_query_matrix(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    query_matrix_id: uuid.UUID | None,
) -> None:
    if query_matrix_id is None:
        return
    query = await db.get(QueryMatrix, query_matrix_id)
    if not query or query.hospital_id != hospital_id:
        raise HTTPException(status_code=400, detail="query_matrix_id does not belong to this hospital")


async def _find_existing_variant(
    db: AsyncSession,
    query_target_id: uuid.UUID,
    query_text: str,
    platform: str,
    language: str,
) -> AIQueryVariant | None:
    result = await db.execute(
        select(AIQueryVariant).where(
            AIQueryVariant.query_target_id == query_target_id,
            AIQueryVariant.query_text == query_text.strip(),
            AIQueryVariant.platform == platform.strip(),
            AIQueryVariant.language == language.strip(),
        )
    )
    return result.scalar_one_or_none()


def _variant_key(query_text: str, platform: str, language: str) -> tuple[str, str, str]:
    return (query_text.strip(), platform.strip(), language.strip())


def _apply_target_update(target: AIQueryTarget, update_data: dict) -> None:
    for field, value in update_data.items():
        setattr(target, field, value)


def _serialize_target(target: AIQueryTarget) -> dict:
    variants = sorted(
        target.variants or [],
        key=lambda item: (not item.is_active, item.created_at.isoformat() if item.created_at else ""),
    )
    active_variant_count = sum(1 for variant in variants if variant.is_active)
    linked_query_matrix_count = sum(1 for variant in variants if variant.query_matrix_id is not None)
    return {
        "id": str(target.id),
        "hospital_id": str(target.hospital_id),
        "name": target.name,
        "target_intent": target.target_intent,
        "region_terms": target.region_terms or [],
        "specialty": target.specialty,
        "condition_or_symptom": target.condition_or_symptom,
        "treatment": target.treatment,
        "decision_criteria": target.decision_criteria or [],
        "patient_language": target.patient_language,
        "platforms": target.platforms or [],
        "competitor_names": target.competitor_names or [],
        "priority": target.priority,
        "status": target.status,
        "target_month": target.target_month,
        "created_by": target.created_by,
        "updated_by": target.updated_by,
        "created_at": target.created_at.isoformat() if target.created_at else None,
        "updated_at": target.updated_at.isoformat() if target.updated_at else None,
        "variants": [_serialize_variant(variant) for variant in variants],
        "summary": {
            "variant_count": len(variants),
            "active_variant_count": active_variant_count,
            "linked_query_matrix_count": linked_query_matrix_count,
            "latest_sov_pct": None,
            "last_measured_at": None,
            "gap_status": None,
            "next_action": "baseline 측정 대기" if active_variant_count > 0 else "variant 추가 필요",
        },
    }


def _serialize_variant(variant: AIQueryVariant) -> dict:
    return {
        "id": str(variant.id),
        "query_target_id": str(variant.query_target_id),
        "query_text": variant.query_text,
        "platform": variant.platform,
        "language": variant.language,
        "is_active": variant.is_active,
        "query_matrix_id": str(variant.query_matrix_id) if variant.query_matrix_id else None,
        "created_at": variant.created_at.isoformat() if variant.created_at else None,
        "updated_at": variant.updated_at.isoformat() if variant.updated_at else None,
    }


def _target_sort_key(target: AIQueryTarget) -> tuple[int, int, str, str]:
    status_rank = {"ACTIVE": 0, "PAUSED": 1, "ARCHIVED": 2}.get(target.status, 9)
    priority_rank = {"HIGH": 0, "NORMAL": 1, "LOW": 2}.get(target.priority, 9)
    created_at = target.created_at.isoformat() if target.created_at else ""
    return (status_rank, priority_rank, target.target_month or "", created_at)
