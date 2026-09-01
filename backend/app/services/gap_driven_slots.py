"""측정된 노출 격차가 월간 슬롯의 유형과 대상 질문을 결정하게 만든다.

## 왜 필요한가

지금까지 월간 콘텐츠 유형은 `PLAN_DISTRIBUTION` 고정 배분을 병원·월 시드로 섞은
결과였고(`content_calendar._interleave_types`), 측정 결과와 아무 관계가 없었다.
"어느 질문에서 우리가 안 나오는가"는 생성 직전에야, 그것도 이미 정해진 유형에
라벨을 붙이는 방식으로만 들어왔다. 이 모듈은 그 순서를 뒤집는다 — **월 계획을 만들
때** 열린 미언급 격차를 보고 슬롯의 유형을 정하고, 그 슬롯이 답할 질문을 그 자리에서
확정(commit)한다.

## 무엇을 보장하는가 (경계)

1. **월 편수는 절대 바뀌지 않는다.** `PLAN_DISTRIBUTION`의 요금제별 총합은 계약이다.
   재배정은 네 유형(FAQ·LOCAL·DISEASE·TREATMENT) 안에서 슬롯을 주고받을 뿐이다.
2. **COLUMN·HEALTH·NOTICE는 건드리지 않는다.** 원장 칼럼·건강 정보·공지는 측정 격차와
   무관한 계약 산출물이다.
3. **재배정 상한은 두 겹이다.**
   - 격차 기반으로 손대는 슬롯은 네 유형 슬롯의 **50%까지**(`GAP_DRIVEN_SLOT_RATIO`).
   - 한 유형이 내줄 수 있는 슬롯은 그 유형 배분의 **절반까지**(`floor(cap/2)`).
     이 하한이 없으면 격차가 많은 달에 FAQ만 남고 질환 가이드가 사라진다.
   - 받는 쪽은 그 요금제에서 한 유형에 허용된 **최대 배분치**를 넘지 않는다
     (PLAN_16이면 4편 — 즉 어떤 달도 같은 요금제가 원래 낼 수 있는 모양을 벗어나지 않는다).
4. **결정론.** 같은 (슬롯, 요금제, 타깃 목록) 입력은 항상 같은 계획을 낸다.

DB는 이 모듈이 직접 읽지 않는다. 호출부(sync 워커 / async Admin API)가 각자
`gap_target_rows_stmt()`로 읽어 `GapTarget` 목록으로 넘긴다.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select

from app.models.content import PLAN_DISTRIBUTION, ContentType
from app.models.sov import AIQueryTarget, ExposureGap
from app.services.content_target_planner import (
    AFFINITY_POSSIBLE,
    MENTION_GAP_RANK,
    NO_MENTION_GAP_RANK,
    OPEN_GAP_STATUSES,
    PRIORITY_RANK,
    _content_type_affinity,
)
from app.services.query_target_structure import apply_structure_to_target

# 격차 기반 재배정 대상 유형. AI 답변에서 병원이 언급될 확률을 직접 움직이는 네 가지다
# (CLAUDE.md 콘텐츠 유형표의 ★★★ 등급).
GAP_DRIVEN_TYPES: tuple[ContentType, ...] = (
    ContentType.FAQ,
    ContentType.DISEASE,
    ContentType.TREATMENT,
    ContentType.LOCAL,
)
# 네 유형 슬롯 중 격차가 결정할 수 있는 비율.
GAP_DRIVEN_SLOT_RATIO = 0.5
# 한 유형이 내줄 수 있는 비율 — 나머지 절반은 요금제 약속으로 남긴다.
MAX_GIVEAWAY_RATIO = 0.5
# 슬롯에 타깃을 미리 확정할 최소 적합도. 이보다 나쁘면 유형과 질문이 어긋나므로
# 생성 시점 계획(content_target_planner)에 맡긴다.
COMMIT_AFFINITY_LIMIT = AFFINITY_POSSIBLE


@dataclass(frozen=True, slots=True)
class GapTarget:
    """열린 언급 격차가 있는 측정 질문 1개. ORM에서 떼어낸 순수 값 객체."""

    id: uuid.UUID
    name: str
    gap_rank: int
    priority_rank: int
    target_intent: str = ""
    region_terms: tuple[str, ...] = ()
    condition_or_symptom: str | None = None
    treatment: str | None = None

    @property
    def sort_key(self) -> tuple:
        return (self.gap_rank, self.priority_rank, self.name, str(self.id))


@dataclass(frozen=True, slots=True)
class SlotPlan:
    """월간 슬롯 1개의 최종 계획."""

    scheduled_date: date
    content_type: ContentType
    sequence_no: int
    total_count: int
    query_target_id: uuid.UUID | None = None
    planning_reason: dict | None = None


@dataclass
class _MutableSlot:
    scheduled_date: date
    content_type: ContentType
    sequence_no: int
    total_count: int
    base_type: ContentType
    query_target_id: uuid.UUID | None = None
    reason: dict | None = field(default=None)


def gap_target_rows_stmt(hospital_id: uuid.UUID):
    """열린 미언급/저언급 격차가 걸린 ACTIVE 타깃 행. sync·async 양쪽이 공유한다."""
    return (
        select(AIQueryTarget, ExposureGap.gap_type)
        .join(ExposureGap, ExposureGap.query_target_id == AIQueryTarget.id)
        .where(
            AIQueryTarget.hospital_id == hospital_id,
            AIQueryTarget.status == "ACTIVE",
            ExposureGap.hospital_id == hospital_id,
            ExposureGap.status.in_(OPEN_GAP_STATUSES),
            ExposureGap.gap_type.in_(tuple(MENTION_GAP_RANK)),
        )
    )


def build_gap_targets(rows) -> list[GapTarget]:
    """(AIQueryTarget, gap_type) 행 → 중복 없는 GapTarget 목록(급한 순 정렬).

    같은 타깃에 격차가 여러 건 열려 있으면 가장 급한 등급을 쓴다.
    """
    best: dict[str, GapTarget] = {}
    for target, gap_type in rows:
        if target is None or getattr(target, "id", None) is None:
            continue
        # 구조 필드가 비어 있으면 유형 적합도가 상수가 된다 — 여기서도 되짚어 채운다.
        apply_structure_to_target(target)
        rank = MENTION_GAP_RANK.get(str(gap_type), NO_MENTION_GAP_RANK)
        key = str(target.id)
        existing = best.get(key)
        if existing is not None and existing.gap_rank <= rank:
            continue
        best[key] = GapTarget(
            id=target.id,
            name=str(getattr(target, "name", "") or ""),
            gap_rank=rank,
            priority_rank=PRIORITY_RANK.get(
                str(getattr(target, "priority", "NORMAL") or "NORMAL").upper(), 9
            ),
            target_intent=str(getattr(target, "target_intent", "") or ""),
            region_terms=tuple(getattr(target, "region_terms", None) or ()),
            condition_or_symptom=getattr(target, "condition_or_symptom", None),
            treatment=getattr(target, "treatment", None),
        )
    return sorted(best.values(), key=lambda item: item.sort_key)


def _type_preference(target: GapTarget) -> tuple[ContentType, ...]:
    """이 질문에 가장 구체적으로 답하는 유형부터의 선호 순서.

    적합도만으로 정렬하면 FAQ가 거의 모든 질문에 0점이라 모든 격차가 FAQ로 몰린다.
    질문이 실제로 담고 있는 것(시술 / 질환·증상 / 지역만)을 먼저 보고, 그 다음에
    범용 유형으로 내려간다.
    """
    if target.treatment:
        return (ContentType.TREATMENT, ContentType.FAQ, ContentType.LOCAL, ContentType.DISEASE)
    if target.condition_or_symptom:
        return (ContentType.DISEASE, ContentType.FAQ, ContentType.LOCAL, ContentType.TREATMENT)
    if target.region_terms:
        # 임상 키워드 없이 "지역 + 진료과"만 묻는 질문 → 지역 특화 글이 정확히 대응한다.
        return (ContentType.LOCAL, ContentType.FAQ, ContentType.DISEASE, ContentType.TREATMENT)
    return (ContentType.FAQ, ContentType.DISEASE, ContentType.TREATMENT, ContentType.LOCAL)


def _best_types(target: GapTarget) -> list[tuple[int, int, ContentType]]:
    """(적합도, 선호 순서, 유형) — 적합도가 같으면 더 구체적인 유형이 앞선다."""
    return sorted(
        (
            (_content_type_affinity(target, content_type), order, content_type)
            for order, content_type in enumerate(_type_preference(target))
        ),
        key=lambda entry: (entry[0], entry[1]),
    )


def plan_gap_driven_slots(
    slots: list[tuple[date, ContentType, int, int]],
    *,
    plan: str,
    gap_targets: list[GapTarget],
) -> list[SlotPlan]:
    """정적 배분으로 만든 슬롯 목록에 격차 기반 유형·타깃 결정을 얹는다.

    격차 타깃이 없으면 입력을 그대로 돌려준다(= 기존 동작).
    """
    plans = [
        _MutableSlot(
            scheduled_date=slot_date,
            content_type=ctype,
            sequence_no=seq_no,
            total_count=total,
            base_type=ctype,
        )
        for slot_date, ctype, seq_no, total in slots
    ]
    if not gap_targets:
        return [_freeze(slot) for slot in plans]

    distribution = PLAN_DISTRIBUTION.get(plan, {})
    pool_indexes = [
        index for index, slot in enumerate(plans) if slot.base_type in GAP_DRIVEN_TYPES
    ]
    if not pool_indexes:
        return [_freeze(slot) for slot in plans]

    # 이번 달 격차가 결정할 수 있는 슬롯 수 (네 유형 슬롯의 50%까지).
    budget = int(len(pool_indexes) * GAP_DRIVEN_SLOT_RATIO)
    if budget <= 0:
        return [_freeze(slot) for slot in plans]

    counts: dict[ContentType, int] = {}
    for index in pool_indexes:
        counts[plans[index].base_type] = counts.get(plans[index].base_type, 0) + 1

    # 받는 쪽 상한: 이 요금제가 한 유형에 허용한 최대 배분치.
    receive_ceiling = max(
        (distribution.get(content_type, 0) for content_type in GAP_DRIVEN_TYPES),
        default=0,
    ) or max(counts.values(), default=0)
    # 내주는 쪽 하한: 유형별 배분의 절반은 반드시 남긴다.
    giveaway_left = {
        content_type: int(counts.get(content_type, 0) * MAX_GIVEAWAY_RATIO)
        for content_type in GAP_DRIVEN_TYPES
    }

    available = list(pool_indexes)
    used = 0
    for target in gap_targets:
        if used >= budget or not available:
            break
        placed = _place_target(
            plans,
            available=available,
            target=target,
            counts=counts,
            giveaway_left=giveaway_left,
            receive_ceiling=receive_ceiling,
        )
        if placed is None:
            continue
        available.remove(placed)
        used += 1

    return [_freeze(slot) for slot in plans]


def _place_target(
    plans: list[_MutableSlot],
    *,
    available: list[int],
    target: GapTarget,
    counts: dict[ContentType, int],
    giveaway_left: dict[ContentType, int],
    receive_ceiling: int,
) -> int | None:
    """타깃 하나를 슬롯 하나에 배정한다. 배정한 슬롯 index 또는 None."""
    for affinity, _order, wanted in _best_types(target):
        if affinity > COMMIT_AFFINITY_LIMIT:
            # 이 타깃은 네 유형 어느 것으로도 제대로 답할 수 없다 — 유형을 바꾸지 않는다.
            break

        # (1) 이미 그 유형인 슬롯이 있으면 유형은 그대로 두고 질문만 확정한다.
        same_type = [index for index in available if plans[index].content_type is wanted]
        if same_type:
            index = same_type[0]
            _commit(plans[index], target, wanted, affinity, retyped=False)
            return index

        # (2) 유형을 바꿔야 한다면 상한을 확인한다.
        if counts.get(wanted, 0) >= receive_ceiling:
            continue
        donors = [
            index
            for index in available
            if plans[index].content_type is not wanted
            and giveaway_left.get(plans[index].content_type, 0) > 0
        ]
        if not donors:
            continue
        # 여유가 가장 많은 유형에서 가져온다 — 편수가 적은 유형이 먼저 사라지지 않게.
        donors.sort(
            key=lambda index: (
                -giveaway_left.get(plans[index].content_type, 0),
                plans[index].sequence_no,
            )
        )
        index = donors[0]
        donor_type = plans[index].content_type
        giveaway_left[donor_type] -= 1
        counts[donor_type] = counts.get(donor_type, 0) - 1
        counts[wanted] = counts.get(wanted, 0) + 1
        _commit(plans[index], target, wanted, affinity, retyped=True)
        return index
    return None


def _commit(
    slot: _MutableSlot,
    target: GapTarget,
    content_type: ContentType,
    affinity: int,
    *,
    retyped: bool,
) -> None:
    slot.content_type = content_type
    slot.query_target_id = target.id
    slot.reason = {
        "mode": "gap_driven_calendar",
        "gap_type": _gap_type_label(target.gap_rank),
        "query_target_id": str(target.id),
        "target_query": target.name,
        "base_content_type": slot.base_type.value,
        "content_type": content_type.value,
        "retyped": retyped,
        "affinity": affinity,
    }


def _gap_type_label(gap_rank: int) -> str:
    for label, rank in MENTION_GAP_RANK.items():
        if rank == gap_rank:
            return label
    return "UNKNOWN"


def _freeze(slot: _MutableSlot) -> SlotPlan:
    return SlotPlan(
        scheduled_date=slot.scheduled_date,
        content_type=slot.content_type,
        sequence_no=slot.sequence_no,
        total_count=slot.total_count,
        query_target_id=slot.query_target_id,
        planning_reason=slot.reason,
    )
