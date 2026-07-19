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
POST   /admin/hospitals/{id}/query-targets/seed-from-matrix
"""
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.sov import (
    AIQueryTarget,
    AIQueryVariant,
    ExposureAction,
    ExposureGap,
    QueryMatrix,
    SovRecord,
)
from app.schemas.query_target import (
    AIQueryTargetCreate,
    AIQueryTargetDetail,
    AIQueryTargetListItem,
    AIQueryTargetUpdate,
    AIQueryVariantCreate,
    AIQueryVariantResponse,
    AIQueryVariantUpdate,
    SUPPORTED_QUERY_PLATFORMS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Patient Questions"])

ARCHIVED = "ARCHIVED"

QUERY_TARGET_PRIORITY_DISPLAY_LABELS = {
    "HIGH": "높음",
    "NORMAL": "보통",
    "LOW": "낮음",
}
QUERY_TARGET_STATUS_DISPLAY_LABELS = {
    "ACTIVE": "운영중",
    "PAUSED": "일시정지",
    "ARCHIVED": "보관",
}
PLATFORM_DISPLAY_LABELS = {
    "CHATGPT": "ChatGPT",
    "GEMINI": "Gemini",
    "GOOGLE_AI_OVERVIEW": "Google AI Overview",
    "PERPLEXITY": "Perplexity",
}
VARIANT_STATUS_DISPLAY_LABELS = {True: "운영중", False: "일시정지"}


def _display_label(labels: dict, value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return labels.get(value.upper(), value)
    return labels.get(value, str(value))


def _platform_label(platform: str | None) -> str | None:
    if platform is None:
        return None
    return PLATFORM_DISPLAY_LABELS.get(str(platform).upper(), str(platform))


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
    summaries = await _load_target_operational_summaries(db, targets)
    return [_serialize_target(target, summaries.get(target.id)) for target in targets]


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

    # 플랫폼을 선택했지만 문구를 따로 입력하지 않은 경우 target.name을 기본 질문으로
    # 사용한다. platforms와 실제 측정 variants가 어긋나 Gemini가 영구 누락되지 않게 한다.
    present_platforms = {key[1] for key in seen_variants}
    for platform in body.platforms:
        if platform in present_platforms:
            continue
        db.add(
            AIQueryVariant(
                query_target_id=target.id,
                query_text=body.name,
                platform=platform,
                language=body.patient_language,
                is_active=True,
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
    summaries = await _load_target_operational_summaries(db, [target])
    return _serialize_target(target, summaries.get(target.id))


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
    "/{hospital_id}/query-targets/seed-from-matrix",
    status_code=status.HTTP_200_OK,
)
async def seed_query_targets_from_matrix_endpoint(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """V0 QueryMatrix에서 AIQueryTarget을 멱등 시드 (AE 수동 재시드용).

    이미 존재하는 query_text는 건너뛰고, SoV 갭(미언급 우선)에 따라 priority를 부여한다.
    V0 리포트 완료 후 노출 보완 탭이 비어 있을 때 재실행하면 된다.
    """
    await _get_hospital_or_404(db, hospital_id)
    result = await seed_query_targets_from_matrix(db, hospital_id)
    return result


async def seed_query_targets_from_matrix(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> dict:
    """QueryMatrix 행에서 AIQueryTarget을 멱등 upsert하는 공유 헬퍼.

    - 멱등 키: (hospital_id, query_text) — 동일 query_text의 기존 target은 건너뜀.
    - 우선순위: 해당 query_matrix 행의 SoV 결과에서 미언급(is_mentioned=False)인 행을
      먼저 HIGH로 생성하고, 나머지는 NORMAL로 생성해 노출 갭이 큰 질문부터 집중한다.
    - 생성·기존 target마다 CHATGPT/GEMINI variant를 보장하고 query_matrix_id를 연결한다.

    반환: {"created": int, "skipped": int}
    """
    import arrow

    # 기존 target도 플랫폼 누락을 고쳐야 하므로 variants까지 읽는다.
    existing_targets_result = await db.execute(
        select(AIQueryTarget)
        .options(selectinload(AIQueryTarget.variants))
        .where(AIQueryTarget.hospital_id == hospital_id)
    )
    existing_by_name: dict[str, AIQueryTarget | None] = {}
    for target in existing_targets_result.scalars().all():
        # 레거시 최소 projection(select name) 결과도 멱등 키로는 사용할 수 있다.
        # 실제 운영 쿼리는 AIQueryTarget 전체 객체라 누락 플랫폼 backfill까지 수행한다.
        if isinstance(target, tuple):
            if target:
                existing_by_name[str(target[0])] = None
        else:
            existing_by_name[target.name] = target

    # 전체 QueryMatrix 행 조회 (is_active 필터 없음 — 시드 시점엔 모두 포함)
    matrix_result = await db.execute(
        select(QueryMatrix).where(QueryMatrix.hospital_id == hospital_id)
    )
    matrix_rows: list[QueryMatrix] = list(matrix_result.scalars().all())

    if not matrix_rows:
        return {"created": 0, "skipped": 0}

    # query_matrix_id별 최근 SoV 언급 여부 집계 — 미언급 질문을 HIGH priority로 올린다
    from app.models.sov import SovRecord

    sov_result = await db.execute(
        select(SovRecord.query_id, SovRecord.is_mentioned).where(
            SovRecord.hospital_id == hospital_id
        )
    )
    # query_id → 언급된 적 있는지 여부
    mentioned_by_query: dict[str, bool] = {}
    for row in sov_result.all():
        qid = str(row.query_id)
        if row.is_mentioned:
            mentioned_by_query[qid] = True
        elif qid not in mentioned_by_query:
            mentioned_by_query[qid] = False

    # 미언급(노출 갭이 큰) 질문을 먼저 생성해 created_at 순서가 우선순위를 반영하도록 정렬한다.
    # mentioned_by_query: True=언급됨 → 뒤로, False/None=미언급/미측정 → 앞으로.
    matrix_rows.sort(key=lambda q: mentioned_by_query.get(str(q.id)) is True)

    now_month = arrow.now("Asia/Seoul").format("YYYY-MM")

    created = 0
    skipped = 0
    backfilled = 0
    for q in matrix_rows:
        # 멱등 체크: 동일 query_text target은 새로 만들지 않고 이중 플랫폼만 보완한다.
        if q.query_text in existing_by_name:
            existing_target = existing_by_name[q.query_text]
            if existing_target is not None:
                changed = _ensure_dual_platform_variants(existing_target, q, db)
                if changed:
                    backfilled += 1
            skipped += 1
            continue

        # SoV 갭 우선순위: 측정 결과 없거나 미언급 → HIGH, 언급된 적 있음 → NORMAL
        is_mentioned = mentioned_by_query.get(str(q.id))
        priority = "NORMAL" if is_mentioned else "HIGH"

        target = AIQueryTarget(
            hospital_id=hospital_id,
            name=q.query_text,
            target_intent="증상 탐색",      # V0 기본값; AE가 추후 편집
            region_terms=[],
            specialty=None,
            condition_or_symptom=None,
            treatment=None,
            decision_criteria=[],
            platforms=["CHATGPT", "GEMINI"],
            competitor_names=[],
            priority=priority,
            status="ACTIVE",
            target_month=now_month,
            created_by="V0 자동 시드",
            updated_by=None,
        )
        db.add(target)
        await db.flush()  # target.id 확정

        for platform in ("CHATGPT", "GEMINI"):
            db.add(
                AIQueryVariant(
                    query_target_id=target.id,
                    query_text=q.query_text,
                    platform=platform,
                    language="ko",
                    is_active=True,
                    query_matrix_id=q.id,
                )
            )

        existing_by_name[q.query_text] = target  # 같은 배치 내 중복 방지
        created += 1

    if created or backfilled:
        await db.commit()

    logger.info(
        "seed_query_targets_from_matrix: hospital=%s created=%d skipped=%d backfilled=%d",
        hospital_id,
        created,
        skipped,
        backfilled,
    )
    return {"created": created, "skipped": skipped, "backfilled": backfilled}


def _ensure_dual_platform_variants(
    target: AIQueryTarget,
    query: QueryMatrix,
    db,
) -> bool:
    changed = False
    platforms = _supported_platforms(target.platforms)
    for platform in ("CHATGPT", "GEMINI"):
        if platform not in platforms:
            platforms.append(platform)
            changed = True

        matching = next(
            (
                variant
                for variant in (target.variants or [])
                if str(variant.platform).upper() == platform
                and variant.query_text.strip() == query.query_text.strip()
            ),
            None,
        )
        if matching is None:
            variant = AIQueryVariant(
                query_target_id=target.id,
                query_text=query.query_text,
                platform=platform,
                language="ko",
                is_active=True,
                query_matrix_id=query.id,
            )
            db.add(variant)
            target.variants.append(variant)
            changed = True
        else:
            if not matching.is_active:
                matching.is_active = True
                changed = True
            if matching.query_matrix_id is None:
                matching.query_matrix_id = query.id
                changed = True
    if target.platforms != platforms:
        target.platforms = platforms
        changed = True
    return changed


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
    platforms = _supported_platforms(target.platforms)
    if body.platform not in platforms:
        target.platforms = [*platforms, body.platform]
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


async def _load_target_operational_summaries(
    db: AsyncSession,
    targets: list[AIQueryTarget],
) -> dict[uuid.UUID, dict]:
    if not targets:
        return {}
    hospital_id = targets[0].hospital_id
    target_ids = [target.id for target in targets]
    query_ids = [
        variant.query_matrix_id
        for target in targets
        for variant in (target.variants or [])
        if variant.query_matrix_id is not None
    ]
    record_scope = SovRecord.ai_query_target_id.in_(target_ids)
    if query_ids:
        record_scope = or_(record_scope, SovRecord.query_id.in_(query_ids))
    records = (await db.execute(
        select(SovRecord)
        .where(SovRecord.hospital_id == hospital_id, record_scope)
        .order_by(SovRecord.measured_at.desc())
    )).scalars().all()
    gaps = (await db.execute(
        select(ExposureGap).where(ExposureGap.query_target_id.in_(target_ids))
    )).scalars().all()
    actions = (await db.execute(
        select(ExposureAction)
        .options(
            selectinload(ExposureAction.query_target),
            selectinload(ExposureAction.gap),
        )
        .where(ExposureAction.query_target_id.in_(target_ids))
    )).scalars().all()
    return _build_target_operational_summaries(targets, records, gaps, actions)


def _build_target_operational_summaries(
    targets: list,
    records: list,
    gaps: list,
    actions: list,
) -> dict[uuid.UUID, dict]:
    targets_by_key = {str(target.id): target for target in targets}
    query_to_target = {
        str(variant.query_matrix_id): str(target.id)
        for target in targets
        for variant in (getattr(target, "variants", None) or [])
        if getattr(variant, "query_matrix_id", None)
    }
    records_by_target: dict[str, list] = {key: [] for key in targets_by_key}
    for record in records:
        direct_id = getattr(record, "ai_query_target_id", None)
        target_key = str(direct_id) if direct_id else query_to_target.get(str(record.query_id))
        if target_key in records_by_target:
            records_by_target[target_key].append(record)

    severity_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    active_gaps_by_target: dict[str, list] = {key: [] for key in targets_by_key}
    for gap in gaps:
        key = str(getattr(gap, "query_target_id", ""))
        if key in active_gaps_by_target and str(getattr(gap, "status", "")).upper() in {"OPEN", "WATCHING"}:
            active_gaps_by_target[key].append(gap)

    active_actions_by_target: dict[str, list] = {key: [] for key in targets_by_key}
    for action in actions:
        key = str(getattr(action, "query_target_id", ""))
        if key in active_actions_by_target and str(getattr(action, "status", "")).upper() in {
            "OPEN", "IN_PROGRESS", "BLOCKED"
        }:
            active_actions_by_target[key].append(action)

    summaries: dict[uuid.UUID, dict] = {}
    for key, target in targets_by_key.items():
        target_records = records_by_target[key]
        latest_record = max(
            target_records,
            key=lambda record: getattr(record, "measured_at", datetime.min),
            default=None,
        )
        latest_records: list = []
        if latest_record is not None:
            latest_run_id = getattr(latest_record, "measurement_run_id", None)
            if latest_run_id is not None:
                latest_records = [
                    record
                    for record in target_records
                    if getattr(record, "measurement_run_id", None) == latest_run_id
                ]
            else:
                latest_records = [latest_record]
        successful = [record for record in latest_records if _successful_record(record)]
        latest_sov = (
            round(sum(1 for record in successful if record.is_mentioned) / len(successful) * 100, 1)
            if successful
            else None
        )
        target_gaps = sorted(
            active_gaps_by_target[key],
            key=lambda gap: severity_rank.get(str(getattr(gap, "severity", "MEDIUM")).upper(), 9),
        )
        target_actions = sorted(
            active_actions_by_target[key],
            key=lambda action: (
                str(getattr(action, "due_month", None) or "9999-99"),
                str(getattr(action, "created_at", "")),
            ),
        )
        summaries[target.id] = {
            "latest_sov_pct": latest_sov,
            "last_measured_at": (
                latest_record.measured_at.isoformat() if latest_record is not None else None
            ),
            "gap_status": target_gaps[0].status if target_gaps else None,
            "next_action": target_actions[0].title if target_actions else None,
        }
    return summaries


def _successful_record(record) -> bool:
    status = getattr(record, "measurement_status", None)
    if str(status or "SUCCESS").upper() == "FAILED":
        return False
    if hasattr(record, "raw_response"):
        return bool(str(getattr(record, "raw_response", "") or "").strip())
    return True


def _serialize_target(target: AIQueryTarget, operational_summary: dict | None = None) -> dict:
    variants = sorted(
        [
            variant
            for variant in (target.variants or [])
            if str(variant.platform).strip().upper() in SUPPORTED_QUERY_PLATFORMS
        ],
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
        "platforms": _supported_platforms(target.platforms),
        "competitor_names": target.competitor_names or [],
        "priority": target.priority,
        "status": target.status,
        "display": _serialize_target_display(target),
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
            "latest_sov_pct": (operational_summary or {}).get("latest_sov_pct"),
            "last_measured_at": (operational_summary or {}).get("last_measured_at"),
            "gap_status": (operational_summary or {}).get("gap_status"),
            "next_action": (operational_summary or {}).get("next_action") or (
                "첫 AI 언급률 측정 대기" if active_variant_count > 0 else "질문 문구 추가 필요"
            ),
        },
    }


def _serialize_target_display(target: AIQueryTarget) -> dict:
    platforms = _supported_platforms(target.platforms)
    return {
        "priority_label": _display_label(QUERY_TARGET_PRIORITY_DISPLAY_LABELS, target.priority),
        "status_label": _display_label(QUERY_TARGET_STATUS_DISPLAY_LABELS, target.status),
        "platform_labels": [_platform_label(platform) for platform in platforms],
    }


def _supported_platforms(platforms: list | None) -> list[str]:
    result: list[str] = []
    for platform in platforms or []:
        normalized = str(platform).strip().upper()
        if normalized in SUPPORTED_QUERY_PLATFORMS and normalized not in result:
            result.append(normalized)
    return result


def _serialize_variant_display(variant: AIQueryVariant) -> dict:
    return {
        "platform_label": _platform_label(variant.platform),
        "status_label": _display_label(VARIANT_STATUS_DISPLAY_LABELS, variant.is_active),
    }


def _serialize_variant(variant: AIQueryVariant) -> dict:
    return {
        "id": str(variant.id),
        "query_target_id": str(variant.query_target_id),
        "query_text": variant.query_text,
        "platform": variant.platform,
        "language": variant.language,
        "is_active": variant.is_active,
        "display": _serialize_variant_display(variant),
        "query_matrix_id": str(variant.query_matrix_id) if variant.query_matrix_id else None,
        "created_at": variant.created_at.isoformat() if variant.created_at else None,
        "updated_at": variant.updated_at.isoformat() if variant.updated_at else None,
    }


def _target_sort_key(target: AIQueryTarget) -> tuple[int, int, str, str]:
    status_rank = {"ACTIVE": 0, "PAUSED": 1, "ARCHIVED": 2}.get(target.status, 9)
    priority_rank = {"HIGH": 0, "NORMAL": 1, "LOW": 2}.get(target.priority, 9)
    created_at = target.created_at.isoformat() if target.created_at else ""
    return (status_rank, priority_rank, target.target_month or "", created_at)
