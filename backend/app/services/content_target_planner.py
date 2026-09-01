"""Connect measured patient questions to the next ungenerated content slot."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

import arrow
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.content import ContentItem, ContentType
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.models.sov import AIQueryTarget, ExposureAction, ExposureGap
from app.services.content_brief import (
    BRIEF_STATUS_APPROVED,
    PLANNING_REASON_KEY,
    build_content_brief,
)
from app.services.exposure_content_linker import BRIEF_CAPABLE_ACTION_TYPES
from app.services.query_target_structure import (
    INFO_LIKE_INTENTS,
    apply_structure_to_target,
    target_is_question_form,
)

ACTIVE_ACTION_STATUSES = {"OPEN", "IN_PROGRESS"}
PRIORITY_RANK = {"HIGH": 0, "NORMAL": 1, "LOW": 2}

# 언급 격차를 나타내는 gap 종류. 값이 작을수록 먼저 답한다 — 아예 안 나오는 질문이
# 낮게 나오는 질문보다 급하다.
MENTION_GAP_RANK = {"MISSING_MENTION": 0, "LOW_MENTION_SHARE": 1}
NO_MENTION_GAP_RANK = 2
OPEN_GAP_STATUSES = {"OPEN", "IN_PROGRESS"}


def prepare_automatic_content_brief_sync(
    db: Any,
    *,
    item: ContentItem,
    hospital: Hospital,
    philosophy: HospitalContentPhilosophy,
) -> dict:
    """Create and approve a deterministic brief from the latest exposure state.

    The approved clinic philosophy remains the safety gate. The system only chooses
    which measured patient question the already-scheduled slot should answer; it does
    not invent new hospital facts or bypass publication screening.
    """
    scheduled_date = getattr(item, "scheduled_date", None)
    planned_publish_date = scheduled_date.isoformat() if scheduled_date else None
    if item.brief_status == BRIEF_STATUS_APPROVED and isinstance(item.content_brief, dict):
        return {
            **item.content_brief,
            "planned_publish_date": planned_publish_date,
        }

    # Lightweight stubs and imported legacy rows may not expose the linkage columns.
    # They still receive a philosophy-backed generic brief without attempting DB planning.
    if not hasattr(item, "query_target_id"):
        brief = build_content_brief(
            hospital=hospital,
            content_item=item,
            philosophy=philosophy,
        )
        item.content_brief = brief
        item.brief_status = BRIEF_STATUS_APPROVED
        return brief

    target = _load_target(db, getattr(item, "query_target_id", None), hospital.id)
    if target is None:
        target = _choose_target(db, item=item, hospital_id=hospital.id)
        if target is not None:
            item.query_target_id = target.id

    action = _load_or_choose_action(db, item=item, target=target, hospital_id=hospital.id)
    if action is not None:
        item.exposure_action_id = action.id
        action.linked_content_id = item.id

    brief = build_content_brief(
        hospital=hospital,
        content_item=item,
        query_target=target,
        exposure_action=action,
        philosophy=philosophy,
    )
    brief["source"] = {
        **(brief.get("source") or {}),
        "mode": "automatic_exposure_plan",
        "planned_at": datetime.now(timezone.utc).isoformat(),
    }
    brief["planned_publish_date"] = planned_publish_date
    # 캘린더 생성 시점에 격차 기반으로 유형·타깃을 정한 슬롯은 그 결정 근거를
    # content_brief.planning_reason에 남겨 둔다(별도 컬럼 없이 기존 JSON 사용).
    # 여기서 브리프를 새로 만들 때 그 기록을 잃지 않도록 옮겨 담는다.
    previous_brief = item.content_brief if isinstance(item.content_brief, dict) else {}
    planning_reason = previous_brief.get(PLANNING_REASON_KEY)
    if planning_reason:
        brief[PLANNING_REASON_KEY] = planning_reason
    item.content_brief = brief
    item.brief_status = BRIEF_STATUS_APPROVED
    item.brief_approved_at = datetime.now(timezone.utc)
    item.brief_approved_by = "SYSTEM_EXPOSURE_PLANNER"
    return brief


def _load_target(db: Any, target_id: Any, hospital_id: Any) -> AIQueryTarget | None:
    if not target_id:
        return None
    return db.execute(
        select(AIQueryTarget)
        .options(selectinload(AIQueryTarget.variants))
        .where(
            AIQueryTarget.id == target_id,
            AIQueryTarget.hospital_id == hospital_id,
            AIQueryTarget.status == "ACTIVE",
        )
    ).scalar_one_or_none()


def _choose_target(db: Any, *, item: ContentItem, hospital_id: Any) -> AIQueryTarget | None:
    targets = list(
        db.execute(
            select(AIQueryTarget)
            .options(selectinload(AIQueryTarget.variants))
            .where(
                AIQueryTarget.hospital_id == hospital_id,
                AIQueryTarget.status == "ACTIVE",
            )
        )
        .scalars()
        .all()
    )
    if not targets:
        return None

    # 레거시 target은 구조 필드가 비어 있어 유형 친화도가 상수가 된다. 여기서 문장으로
    # 되짚어 채운다 — 별도 백필 스크립트를 돌리지 않아도 운영이 스스로 회복하도록.
    # 결정적이며 빈 필드만 채우므로 재실행해도 결과가 같다.
    for target in targets:
        apply_structure_to_target(target)

    slot_date = item.scheduled_date or date.today()
    month_start = slot_date.replace(day=1)
    month_end = arrow.get(month_start).ceil("month").date()
    linked_ids = list(
        db.execute(
            select(ContentItem.query_target_id).where(
                ContentItem.hospital_id == hospital_id,
                ContentItem.id != item.id,
                ContentItem.scheduled_date >= month_start,
                ContentItem.scheduled_date <= month_end,
                ContentItem.query_target_id.is_not(None),
            )
        ).scalars()
    )
    usage = Counter(str(value) for value in linked_ids if value)

    action_target_ids = set(
        str(value)
        for value in db.execute(
            select(ExposureAction.query_target_id).where(
                ExposureAction.hospital_id == hospital_id,
                ExposureAction.status.in_(ACTIVE_ACTION_STATUSES),
                ExposureAction.action_type.in_(BRIEF_CAPABLE_ACTION_TYPES),
                ExposureAction.linked_content_id.is_(None),
                ExposureAction.query_target_id.is_not(None),
            )
        ).scalars()
        if value
    )
    gap_rank = _mention_gap_rank(db, hospital_id=hospital_id)
    slot_month = slot_date.strftime("%Y-%m")
    # 정렬 순서가 곧 제품 정책이다.
    #   1) 아직 콘텐츠에 연결되지 않은 노출 액션이 열려 있는 타깃
    #   2) 미언급(MISSING_MENTION) → 낮은 언급률(LOW_MENTION_SHARE) → 격차 없음
    #   3) 측정 우선순위(HIGH/NORMAL/LOW)
    #   4) 이 슬롯 유형이 실제로 답할 수 있는 질문인가(유형 친화도)
    #   5) 이번 달 사용 횟수 — 동률 안에서 라운드로빈해 한 질문에 몰리지 않게
    # 4를 5보다 앞에 둔 것이 이번 변경의 핵심이다. 예전에는 사용 횟수가 먼저라
    # 유형과 전혀 맞지 않는 타깃이 먼저 소진됐다.
    return min(
        targets,
        key=lambda target: (
            0 if str(target.id) in action_target_ids else 1,
            gap_rank.get(str(target.id), NO_MENTION_GAP_RANK),
            PRIORITY_RANK.get(str(target.priority or "NORMAL").upper(), 9),
            _content_type_affinity(target, item.content_type),
            usage[str(target.id)],
            0 if target.target_month == slot_month else 1,
            str(target.name or ""),
            str(target.id),
        ),
    )


def _mention_gap_rank(db: Any, *, hospital_id: Any) -> dict[str, int]:
    """열린 언급 격차가 있는 타깃 → 등급. 없는 타깃은 결과에 없다.

    측정이 "이 질문에서 우리는 안 나온다"고 말한 타깃을 먼저 답하게 만드는 고리다.
    """
    rows = db.execute(
        select(ExposureGap.query_target_id, ExposureGap.gap_type).where(
            ExposureGap.hospital_id == hospital_id,
            ExposureGap.status.in_(OPEN_GAP_STATUSES),
            ExposureGap.gap_type.in_(tuple(MENTION_GAP_RANK)),
            ExposureGap.query_target_id.is_not(None),
        )
    ).all()
    ranked: dict[str, int] = {}
    for target_id, gap_type in rows:
        if not target_id:
            continue
        key = str(target_id)
        rank = MENTION_GAP_RANK.get(str(gap_type), NO_MENTION_GAP_RANK)
        if rank < ranked.get(key, NO_MENTION_GAP_RANK):
            ranked[key] = rank
    return ranked


def _load_or_choose_action(
    db: Any,
    *,
    item: ContentItem,
    target: AIQueryTarget | None,
    hospital_id: Any,
) -> ExposureAction | None:
    exposure_action_id = getattr(item, "exposure_action_id", None)
    if exposure_action_id:
        existing = db.get(ExposureAction, exposure_action_id)
        if existing is not None:
            return existing
    if target is None:
        return None
    actions = list(
        db.execute(
            select(ExposureAction).where(
                ExposureAction.hospital_id == hospital_id,
                ExposureAction.query_target_id == target.id,
                ExposureAction.status.in_(ACTIVE_ACTION_STATUSES),
                ExposureAction.action_type.in_(BRIEF_CAPABLE_ACTION_TYPES),
                ExposureAction.linked_content_id.is_(None),
            )
        )
        .scalars()
        .all()
    )
    if not actions:
        return None
    return sorted(
        actions,
        key=lambda action: (
            str(action.due_month or "9999-99"),
            str(action.created_at or ""),
            str(action.id),
        ),
    )[0]


# 유형 친화도 등급. 0=이 유형이 그 질문에 정확히 답한다, 1=답할 수 있다,
# 3=구조적으로 답이 되지 않는다. 값 자체가 아니라 **타깃 간 순서**가 목적이다.
AFFINITY_EXACT = 0
AFFINITY_POSSIBLE = 1
AFFINITY_WEAK = 2
AFFINITY_MISMATCH = 3


def _content_type_affinity(target: AIQueryTarget, content_type: ContentType | str) -> int:
    """콘텐츠 유형이 이 측정 질문에 답할 수 있는 정도. 작을수록 잘 맞는다.

    예전 구현은 FAQ에 항상 0, COLUMN에 항상 1을 돌려줬고 나머지도 시드가 비워 둔
    필드만 봤기 때문에 **모든 타깃에서 같은 값**이었다. 즉 정렬에 아무 기여를 못 했다.
    지금은 질의에서 되찾은 구조(지역·질환·시술·의도·질문형 여부)를 실제로 본다.
    """
    value = content_type.value if hasattr(content_type, "value") else str(content_type)
    intent = str(getattr(target, "target_intent", "") or "")
    region_terms = list(getattr(target, "region_terms", None) or [])
    condition = getattr(target, "condition_or_symptom", None)
    treatment = getattr(target, "treatment", None)

    if value == ContentType.FAQ.value:
        # FAQ는 "환자가 묻는 문장"에 그대로 대응한다. 측정 질의는 대부분 질문형이라
        # 넓게 맞지만, 질문형이 아닌 타깃(키워드 나열 등)에는 밀린다.
        return AFFINITY_EXACT if target_is_question_form(target) else AFFINITY_WEAK
    if value == ContentType.LOCAL.value:
        # 지역이 없는 질문에 지역 특화 글을 붙이면 측정 질의와 글이 어긋난다.
        return AFFINITY_EXACT if region_terms else AFFINITY_MISMATCH
    if value == ContentType.TREATMENT.value:
        if treatment:
            return AFFINITY_EXACT
        return AFFINITY_POSSIBLE if condition else AFFINITY_MISMATCH
    if value == ContentType.DISEASE.value:
        if condition:
            return AFFINITY_EXACT
        return AFFINITY_POSSIBLE if treatment else AFFINITY_MISMATCH
    if value == ContentType.HEALTH.value:
        # 건강 정보·칼럼은 "설명하는 글"이다. 비용·비교·정보형 질문에 붙는다.
        if intent in INFO_LIKE_INTENTS:
            return AFFINITY_EXACT
        return AFFINITY_POSSIBLE if condition else AFFINITY_WEAK
    if value == ContentType.COLUMN.value:
        if intent in INFO_LIKE_INTENTS:
            return AFFINITY_EXACT
        return AFFINITY_WEAK
    # NOTICE는 병원 운영 공지다. 측정 질의를 답하는 유형이 아니다.
    return AFFINITY_MISMATCH
