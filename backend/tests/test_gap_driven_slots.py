"""격차 기반 월간 슬롯 재배정 테스트.

지켜야 하는 경계(gap_driven_slots 모듈 docstring):
- 요금제별 월 편수 총합 불변 (CLAUDE.md 요금제 표)
- COLUMN·HEALTH·NOTICE 편수 불변
- 재배정은 네 유형 슬롯의 50%까지, 한 유형이 내주는 슬롯은 그 유형 배분의 절반까지
- 받는 유형은 그 요금제의 최대 단일 배분치를 넘지 않는다
- 결정론
"""
import os
import uuid
from collections import Counter

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from types import SimpleNamespace

import arrow
import pytest

from app.models.content import PLAN_DISTRIBUTION, ContentType
from app.services.content_calendar import generate_monthly_slots
from app.services.gap_driven_slots import (
    GAP_DRIVEN_TYPES,
    ExistingSlot,
    GapTarget,
    build_gap_targets,
    plan_gap_driven_slots,
)

PLANS = ("PLAN_20", "PLAN_16", "PLAN_12")
UNTOUCHED_TYPES = (ContentType.COLUMN, ContentType.HEALTH, ContentType.NOTICE)


def _slots(plan: str):
    return generate_monthly_slots(
        plan,
        [0, 1, 2, 3, 4],
        arrow.get("2026-10-01"),
        hospital_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
    )


def _local_targets(count: int) -> list[GapTarget]:
    """전부 '지역+진료과' 질문 — 지역 특화(LOCAL) 수요가 한쪽으로 쏠린 달."""
    return [
        GapTarget(
            id=uuid.uuid4(),
            name=f"강남{index}역 정형외과 병원 추천해줘",
            gap_rank=0,
            priority_rank=0,
            target_intent="추천 탐색",
            region_terms=(f"강남{index}역",),
        )
        for index in range(count)
    ]


def _mixed_targets(count: int) -> list[GapTarget]:
    samples = [
        ("강남역 정형외과 병원 추천해줘", None, None),
        ("강남역에서 허리디스크 치료하는 병원 알려줘", "허리디스크", None),
        ("역삼동 대장내시경 가능한 병원 추천해줘", None, "대장내시경"),
        ("역삼동 오십견 진료 가능한 병원", "오십견", None),
    ]
    targets = []
    for index in range(count):
        name, condition, treatment = samples[index % len(samples)]
        targets.append(
            GapTarget(
                id=uuid.uuid4(),
                name=f"{name} #{index}",
                gap_rank=0,
                priority_rank=0,
                target_intent="증상 탐색",
                region_terms=("강남역",),
                condition_or_symptom=condition,
                treatment=treatment,
            )
        )
    return targets


@pytest.mark.parametrize("plan", PLANS)
def test_no_gap_targets_keeps_the_static_distribution(plan):
    slots = _slots(plan)
    planned = plan_gap_driven_slots(slots, plan=plan, gap_targets=[])

    assert [(p.scheduled_date, p.content_type, p.sequence_no) for p in planned] == [
        (slot[0], slot[1], slot[2]) for slot in slots
    ]
    assert all(p.query_target_id is None for p in planned)


@pytest.mark.parametrize("plan", PLANS)
def test_monthly_totals_never_change(plan):
    slots = _slots(plan)
    planned = plan_gap_driven_slots(slots, plan=plan, gap_targets=_local_targets(10))

    assert len(planned) == sum(PLAN_DISTRIBUTION[plan].values())
    assert {p.sequence_no for p in planned} == {slot[2] for slot in slots}
    assert [p.scheduled_date for p in planned] == [slot[0] for slot in slots]


@pytest.mark.parametrize("plan", PLANS)
def test_column_health_notice_counts_are_untouched(plan):
    slots = _slots(plan)
    planned = plan_gap_driven_slots(slots, plan=plan, gap_targets=_local_targets(10))
    after = Counter(p.content_type for p in planned)

    for content_type in UNTOUCHED_TYPES:
        assert after[content_type] == PLAN_DISTRIBUTION[plan].get(content_type, 0)
    assert all(
        p.query_target_id is None
        for p in planned
        if p.content_type in UNTOUCHED_TYPES
    )


@pytest.mark.parametrize("plan", PLANS)
def test_retyping_stays_within_caps(plan):
    slots = _slots(plan)
    base = Counter(slot[1] for slot in slots)
    planned = plan_gap_driven_slots(slots, plan=plan, gap_targets=_local_targets(10))
    after = Counter(p.content_type for p in planned)

    ceiling = max(PLAN_DISTRIBUTION[plan].get(t, 0) for t in GAP_DRIVEN_TYPES)
    for content_type in GAP_DRIVEN_TYPES:
        # 받는 쪽: 이 요금제의 최대 단일 배분치를 넘지 않는다.
        assert after[content_type] <= ceiling
        # 내주는 쪽: 배분의 절반은 남는다.
        assert after[content_type] >= base[content_type] - int(base[content_type] * 0.5)


@pytest.mark.parametrize("plan", PLANS)
def test_gap_driven_slots_are_capped_at_half_the_pool(plan):
    slots = _slots(plan)
    pool_size = sum(1 for slot in slots if slot[1] in GAP_DRIVEN_TYPES)
    planned = plan_gap_driven_slots(slots, plan=plan, gap_targets=_local_targets(20))

    committed = [p for p in planned if p.query_target_id]
    assert len(committed) == int(pool_size * 0.5)
    assert len({p.query_target_id for p in committed}) == len(committed)


def test_plan_16_with_ten_missing_targets_commits_five_slots():
    """리뷰 보고용 수치 고정: PLAN_16(16편) 중 격차가 결정하는 슬롯 수."""
    slots = _slots("PLAN_16")
    planned = plan_gap_driven_slots(slots, plan="PLAN_16", gap_targets=_mixed_targets(10))

    committed = [p for p in planned if p.query_target_id]
    # 네 유형 슬롯 11개(FAQ4+DISEASE3+TREATMENT3+LOCAL1)의 50% = 5
    assert len(committed) == 5
    assert len(planned) == 16


def test_skewed_demand_actually_retypes_slots():
    """수요가 한 유형으로 쏠리면 유형이 실제로 바뀐다(라벨만 붙이는 게 아니다)."""
    slots = _slots("PLAN_16")
    planned = plan_gap_driven_slots(slots, plan="PLAN_16", gap_targets=_local_targets(10))

    retyped = [p for p in planned if p.planning_reason and p.planning_reason["retyped"]]
    assert retyped, "쏠린 수요에서는 재배정이 일어나야 한다"
    assert Counter(p.content_type for p in planned)[ContentType.LOCAL] > 1


def test_planning_reason_records_the_decision():
    slots = _slots("PLAN_16")
    planned = plan_gap_driven_slots(slots, plan="PLAN_16", gap_targets=_local_targets(10))

    reasons = [p.planning_reason for p in planned if p.planning_reason]
    assert reasons
    reason = reasons[0]
    assert reason["mode"] == "gap_driven_calendar"
    assert reason["gap_type"] == "MISSING_MENTION"
    assert reason["base_content_type"] in {t.value for t in GAP_DRIVEN_TYPES}
    assert reason["content_type"] in {t.value for t in GAP_DRIVEN_TYPES}


def test_planning_is_deterministic():
    slots = _slots("PLAN_20")
    targets = _mixed_targets(8)

    first = plan_gap_driven_slots(slots, plan="PLAN_20", gap_targets=targets)
    second = plan_gap_driven_slots(slots, plan="PLAN_20", gap_targets=targets)

    assert first == second


# ── build_gap_targets ─────────────────────────────────────────────────────────


def _orm_target(name: str, priority: str = "HIGH"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        priority=priority,
        target_intent="증상 탐색",
        region_terms=[],
        specialty=None,
        condition_or_symptom=None,
        treatment=None,
    )


def test_build_gap_targets_backfills_structure_and_sorts_missing_first():
    low = _orm_target("역삼동 오십견 진료 가능한 병원", priority="NORMAL")
    missing = _orm_target("강남역에서 허리디스크 치료하는 병원 알려줘")

    targets = build_gap_targets(
        [(low, "LOW_MENTION_SHARE"), (missing, "MISSING_MENTION")]
    )

    assert [target.id for target in targets] == [missing.id, low.id]
    # 구조 필드가 비어 있던 행도 여기서 채워져 유형 적합도가 변별력을 갖는다.
    assert targets[0].condition_or_symptom == "허리디스크"
    assert targets[0].region_terms == ("강남역",)


def test_build_gap_targets_dedupes_to_the_most_urgent_gap():
    target = _orm_target("역삼동 오십견 진료 가능한 병원")

    targets = build_gap_targets(
        [(target, "LOW_MENTION_SHARE"), (target, "MISSING_MENTION")]
    )

    assert len(targets) == 1
    assert targets[0].gap_rank == 0


# ── 재실행: 상한은 한 달에 한 번만 소진된다 ──────────────────────────────────


def _two_run_union(plan: str, first_targets, second_targets, first_batch_size: int):
    """부분 생성(1회차) 뒤 남은 순번만 채우는 재실행(2회차)을 그대로 재현한다.

    monthly_slots.create_next_month_slots_for_schedule과 같은 순서다:
    월 전체를 계획 → 이미 저장된 순번을 걸러내고 나머지만 저장.
    """
    slots = _slots(plan)
    first = plan_gap_driven_slots(slots, plan=plan, gap_targets=first_targets)
    stored = [p for p in first if p.sequence_no <= first_batch_size]
    existing = [
        ExistingSlot(
            sequence_no=p.sequence_no,
            content_type=p.content_type,
            gap_driven=p.query_target_id is not None,
        )
        for p in stored
    ]
    second = plan_gap_driven_slots(
        slots, plan=plan, gap_targets=second_targets, existing=existing
    )
    union = stored + [p for p in second if p.sequence_no > first_batch_size]
    return slots, union


@pytest.mark.parametrize("plan", PLANS)
def test_rerun_with_new_targets_stays_within_caps(plan):
    """1회차 결과 + 2회차 결과의 합집합도 재배정 상한 안에 있어야 한다.

    2회차의 격차 목록은 1회차와 다르다(측정이 계속 돌기 때문). 그래서 월 전체를
    다시 계획하면 1회차가 이미 쓴 예산을 모르는 채 처음부터 다시 쓰게 된다.
    """
    slots, union = _two_run_union(plan, _local_targets(10), _mixed_targets(10), 8)
    base = Counter(slot[1] for slot in slots)
    after = Counter(p.content_type for p in union)

    assert len(union) == len(slots)
    assert {p.sequence_no for p in union} == {slot[2] for slot in slots}

    pool_size = sum(1 for slot in slots if slot[1] in GAP_DRIVEN_TYPES)
    committed = [p for p in union if p.query_target_id]
    assert len(committed) <= int(pool_size * 0.5)

    ceiling = max(PLAN_DISTRIBUTION[plan].get(t, 0) for t in GAP_DRIVEN_TYPES)
    for content_type in GAP_DRIVEN_TYPES:
        assert after[content_type] <= ceiling
        assert after[content_type] >= base[content_type] - int(base[content_type] * 0.5)

    for content_type in UNTOUCHED_TYPES:
        assert after[content_type] == PLAN_DISTRIBUTION[plan].get(content_type, 0)


def test_rerun_without_existing_slots_exceeds_the_half_pool_budget():
    """`existing`을 넘기지 않으면 50% 예산이 두 번 소진된다 — 회귀를 고정한다."""
    plan = "PLAN_20"
    slots = _slots(plan)
    pool_size = sum(1 for slot in slots if slot[1] in GAP_DRIVEN_TYPES)
    budget = int(pool_size * 0.5)

    first = plan_gap_driven_slots(slots, plan=plan, gap_targets=_local_targets(10))
    stored = [p for p in first if p.sequence_no <= 8]
    # 재실행이 이미 만들어진 슬롯을 모른 채 월 전체를 다시 계획하는 과거 동작.
    naive = plan_gap_driven_slots(slots, plan=plan, gap_targets=_mixed_targets(10))
    naive_union = stored + [p for p in naive if p.sequence_no > 8]
    naive_committed = [p for p in naive_union if p.query_target_id]
    assert len(naive_committed) > budget, "이 시나리오가 상한을 깨야 회귀 테스트가 성립한다"

    _, guarded_union = _two_run_union(plan, _local_targets(10), _mixed_targets(10), 8)
    guarded_committed = [p for p in guarded_union if p.query_target_id]
    assert len(guarded_committed) <= budget


def test_rerun_keeps_already_stored_slot_types_and_targets():
    """이미 저장된 순번은 2회차 계획이 다시 건드리지 않는다."""
    plan = "PLAN_16"
    slots = _slots(plan)
    first = plan_gap_driven_slots(slots, plan=plan, gap_targets=_local_targets(10))
    stored = {p.sequence_no: p for p in first if p.sequence_no <= 5}
    existing = [
        ExistingSlot(
            sequence_no=p.sequence_no,
            content_type=p.content_type,
            gap_driven=p.query_target_id is not None,
        )
        for p in stored.values()
    ]

    second = plan_gap_driven_slots(
        slots, plan=plan, gap_targets=_mixed_targets(8), existing=existing
    )
    replanned = {p.sequence_no: p for p in second if p.sequence_no in stored}

    for sequence_no, kept in stored.items():
        assert replanned[sequence_no].content_type is kept.content_type
        # 저장된 슬롯에는 새 타깃이 붙지 않는다.
        assert replanned[sequence_no].query_target_id is None
    new_targets = {
        p.query_target_id for p in second if p.query_target_id and p.sequence_no not in stored
    }
    assert len(new_targets) == len([p for p in second if p.query_target_id])
