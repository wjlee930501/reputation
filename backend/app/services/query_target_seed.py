"""QueryMatrix → AIQueryTarget 시드 서비스.

환자 질문은 사람이 만들지 않는다. V0 진단이 끝나면 시스템이 QueryMatrix에서
AIQueryTarget을 시드하고, admin 화면은 그 결과를 보기만 한다. 그래서 시드 로직은
admin API가 아니라 이 서비스에 둔다(워커가 admin 라우터를 import하지 않게 한다).
"""
import logging
import uuid

import arrow
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.sov import AIQueryTarget, AIQueryVariant, QueryMatrix, SovRecord
from app.schemas.query_target import SUPPORTED_QUERY_PLATFORMS
from app.services import sov_engine
from app.services.query_target_structure import (
    apply_structure_to_target,
    describe_query_text,
)

logger = logging.getLogger(__name__)

ARCHIVED = "ARCHIVED"


def supported_platforms(platforms: list | None) -> list[str]:
    result: list[str] = []
    for platform in platforms or []:
        normalized = str(platform).strip().upper()
        if normalized in SUPPORTED_QUERY_PLATFORMS and normalized not in result:
            result.append(normalized)
    return result


async def count_live_query_targets(db: AsyncSession, hospital_id: uuid.UUID) -> int:
    """보관(ARCHIVED)되지 않은 환자 질문 수.

    "질문이 하나도 없을 때만 시드한다"는 판단의 근거다. 보관은 '더 이상 재지 않기로 한
    질문'이므로 세지 않는다.
    """
    result = await db.execute(
        select(func.count())
        .select_from(AIQueryTarget)
        .where(
            AIQueryTarget.hospital_id == hospital_id,
            AIQueryTarget.status != ARCHIVED,
        )
    )
    return int(result.scalar_one() or 0)


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

    # **활성 행만** 시드한다. 비활성은 "더 이상 재지 않기로 한 질의"이므로, 여기서
    # 걸르지 않으면 폐기한 질의가 target·variant로 되살아나 주간 측정에 다시 들어간다
    # (변형 경로는 매트릭스의 is_active를 보지 않고 variant.query_text로 측정한다).
    matrix_result = await db.execute(
        select(QueryMatrix).where(
            QueryMatrix.hospital_id == hospital_id,
            QueryMatrix.is_active,
            QueryMatrix.query_intent != sov_engine.QUERY_INTENT_INFO,
        )
    )
    matrix_rows: list[QueryMatrix] = list(matrix_result.scalars().all())

    if not matrix_rows:
        return {"created": 0, "skipped": 0}

    # query_matrix_id별 최근 SoV 언급 여부 집계 — 미언급 질문을 HIGH priority로 올린다
    sov_result = await db.execute(
        select(SovRecord.query_id, SovRecord.is_mentioned).where(
            SovRecord.hospital_id == hospital_id
        )
    )
    # query_id → 언급된 적 있는지 여부. 판정 보류(None)는 언급도 미언급도 아니므로
    # 건너뛴다 — False로 접으면 보류만 있는 질문이 '미언급 HIGH'로 승격된다.
    mentioned_by_query: dict[str, bool] = {}
    for row in sov_result.all():
        if row.is_mentioned is None:
            continue
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
                # 이미 만들어진 target의 빈 구조 필드도 여기서 채운다. 재시드는 멱등해야
                # 하므로 AE가 손으로 넣은 값은 건드리지 않는다(빈 필드만 채움).
                changed = apply_structure_to_target(existing_target) or changed
                if changed:
                    backfilled += 1
            skipped += 1
            continue

        # SoV 갭 우선순위: 측정 결과 없거나 미언급 → HIGH, 언급된 적 있음 → NORMAL
        is_mentioned = mentioned_by_query.get(str(q.id))
        priority = "NORMAL" if is_mentioned else "HIGH"

        # 질의 문장에서 지역·진료과·임상 키워드·환자 의도를 되찾아 구조 필드를 채운다.
        # 예전에는 전부 빈 값("증상 탐색"/[]/None)이라 콘텐츠 계획이 타깃을 구분하지
        # 못했다 — 어떤 슬롯이 어떤 질문을 답할지가 사실상 무작위였다.
        # 질의는 sov_engine 템플릿으로 만들어졌으므로 병원 프로파일을 다시 읽지 않고
        # 문장만으로 복원할 수 있다(= 이 함수의 DB 접근 횟수는 그대로다).
        structure = describe_query_text(q.query_text)

        target = AIQueryTarget(
            hospital_id=hospital_id,
            name=q.query_text,
            target_intent=structure.target_intent,
            region_terms=list(structure.region_terms),
            specialty=structure.specialty,
            condition_or_symptom=structure.condition_or_symptom,
            treatment=structure.treatment,
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
    platforms = supported_platforms(target.platforms)
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
