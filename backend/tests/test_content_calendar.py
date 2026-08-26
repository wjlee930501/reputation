"""콘텐츠 캘린더 유형 배치 회귀 — 몰림 방지 + 결정론.

버그: dict 순서대로 extend하던 이전 로직은 PLAN_16의 첫 4개 슬롯이 전부 FAQ로
연속 배정됐다. 수정 후에는 유형이 고르게 흩뿌려지고, 같은 입력에는 항상 같은
배치를 반환해야 한다(테스트 가능한 결정론).
"""
import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

import uuid  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from itertools import groupby  # noqa: E402

import arrow  # noqa: E402
import pytest  # noqa: E402

from app.models.content import PLAN_DISTRIBUTION, ContentType  # noqa: E402
from app.services.content_calendar import (  # noqa: E402
    _interleave_types,
    allocate_stacked_dates,
    generate_monthly_slots,
)

# 계약 표 (CLAUDE.md "요금제별 월간 편수 배분")를 테스트가 독립적으로 들고 있는다.
# PLAN_DISTRIBUTION을 그대로 기대값으로 쓰면 배분이 잘못 바뀌어도 자기참조라 통과한다.
EXPECTED_DISTRIBUTION = {
    "PLAN_20": {"FAQ": 5, "DISEASE": 4, "TREATMENT": 4, "COLUMN": 2, "HEALTH": 2, "LOCAL": 2, "NOTICE": 1},
    "PLAN_16": {"FAQ": 4, "DISEASE": 3, "TREATMENT": 3, "COLUMN": 2, "HEALTH": 2, "LOCAL": 1, "NOTICE": 1},
    "PLAN_12": {"FAQ": 3, "DISEASE": 3, "TREATMENT": 2, "COLUMN": 2, "HEALTH": 1, "LOCAL": 1, "NOTICE": 0},
}
ALL_PLANS = sorted(EXPECTED_DISTRIBUTION)


def _expected(plan: str) -> dict:
    return {ContentType[name]: count for name, count in EXPECTED_DISTRIBUTION[plan].items()}


def _monthly_count(plan: str) -> int:
    return int(plan.removeprefix("PLAN_"))


def _max_consecutive_run(sequence: list) -> int:
    return max(len(list(group)) for _, group in groupby(sequence))


def test_plan_distribution_matches_the_contracted_plan_table():
    """요금제 배분 상수 자체가 계약 표와 일치한다 — 총합이 맞아도 유형이 틀리면 실패."""
    assert {plan: _expected(plan) for plan in ALL_PLANS} == PLAN_DISTRIBUTION


@pytest.mark.parametrize("plan", ALL_PLANS)
def test_plan_distribution_totals_match_the_plans_monthly_volume(plan):
    assert sum(_expected(plan).values()) == _monthly_count(plan)


@pytest.mark.parametrize("plan", ALL_PLANS)
def test_interleave_types_preserves_every_types_count(plan):
    """유형별 편수 보존은 모든 요금제에서 성립해야 한다.

    총합만 확인하면 FAQ 하나를 늘리고 NOTICE 하나를 줄이는 배분 오류가 통과한다.
    """
    expected = _expected(plan)
    sequence = _interleave_types(PLAN_DISTRIBUTION[plan], seed=f"hospital-a:2026-07:{plan}")

    assert len(sequence) == _monthly_count(plan)
    for ctype, count in expected.items():
        assert sequence.count(ctype) == count, f"{plan}/{ctype} 편수가 계약 표와 다르다"


@pytest.mark.parametrize("plan", ALL_PLANS)
def test_interleave_types_avoids_long_runs(plan):
    # 버그 재현 방지: 이전 로직은 PLAN_16의 첫 4슬롯이 전부 FAQ로 몰렸다.
    sequence = _interleave_types(PLAN_DISTRIBUTION[plan], seed=f"hospital-a:2026-07:{plan}")

    assert _max_consecutive_run(sequence) <= 2


@pytest.mark.parametrize("plan", ALL_PLANS)
def test_generate_monthly_slots_preserves_every_types_count(plan):
    """캘린더 슬롯 생성까지 통과했을 때도 유형별 편수가 계약 표와 일치해야 한다."""
    slots = generate_monthly_slots(
        plan, [0, 1, 2, 3, 4, 5, 6], arrow.get("2026-07-01").floor("month")
    )
    types = [ctype for _, ctype, _, _ in slots]

    assert len(types) == _monthly_count(plan)
    for ctype, count in _expected(plan).items():
        assert types.count(ctype) == count, f"{plan}/{ctype} 편수가 계약 표와 다르다"


def test_interleave_types_is_deterministic_for_same_seed():
    seq1 = _interleave_types(PLAN_DISTRIBUTION["PLAN_16"], seed="hospital-a:2026-07")
    seq2 = _interleave_types(PLAN_DISTRIBUTION["PLAN_16"], seed="hospital-a:2026-07")

    assert seq1 == seq2


def test_interleave_types_differs_across_seeds():
    seq_a = _interleave_types(PLAN_DISTRIBUTION["PLAN_16"], seed="hospital-a:2026-07")
    seq_b = _interleave_types(PLAN_DISTRIBUTION["PLAN_16"], seed="hospital-b:2026-07")

    # 회전량이 시드에 따라 달라져 병원마다 시작 유형이 반복되지 않는다.
    assert seq_a != seq_b


def test_generate_monthly_slots_avoids_long_type_runs_without_hospital_id():
    """hospital_id를 넘기지 않는 기존 호출부(admin API, worker, demo_seed)도
    plan+연월 시드로 자동으로 몰림이 해소되어야 한다."""
    slots = generate_monthly_slots(
        "PLAN_16", [0, 1, 2, 3, 4, 5, 6], arrow.get("2026-07-01").floor("month")
    )
    types = [ctype for _, ctype, _, _ in slots]

    assert len(types) == 16
    assert _max_consecutive_run(types) <= 2


def test_generate_monthly_slots_same_hospital_and_month_is_deterministic():
    hospital_id = uuid.uuid4()
    month = arrow.get("2026-07-01").floor("month")

    slots1 = generate_monthly_slots(
        "PLAN_12", [0, 2, 4], month, hospital_id=hospital_id
    )
    slots2 = generate_monthly_slots(
        "PLAN_12", [0, 2, 4], month, hospital_id=hospital_id
    )

    assert slots1 == slots2


def test_generate_monthly_slots_preserves_dates_and_sequence_numbers():
    slots = generate_monthly_slots(
        "PLAN_12", [0, 1, 2, 3, 4, 5, 6], arrow.get("2026-07-01").floor("month")
    )

    assert [seq for _, _, seq, _ in slots] == list(range(1, 13))
    assert all(total == 12 for _, _, _, total in slots)
    dates = [d for d, _, _, _ in slots]
    assert dates == sorted(dates)
    # PLAN_12 + 모든 요일 허용이어도 월초 12일에 몰리지 않고 월 전체에 분산한다.
    assert dates[0].isoformat() == "2026-07-01"
    assert dates[-1].isoformat() == "2026-07-31"
    assert max((right - left).days for left, right in zip(dates, dates[1:])) <= 3
    assert min((right - left).days for left, right in zip(dates, dates[1:])) >= 2


def test_generate_monthly_slots_spreads_across_allowed_weekdays():
    slots = generate_monthly_slots(
        "PLAN_12", [0, 2, 4], arrow.get("2026-08-01").floor("month")
    )

    dates = [d for d, *_rest in slots]
    assert len(dates) == 12
    assert all(d.weekday() in {0, 2, 4} for d in dates)
    assert dates[0].day <= 7
    assert dates[-1].day >= 25


def test_generate_monthly_slots_allows_positive_worker_shortfall():
    slots = generate_monthly_slots(
        "PLAN_12",
        [1, 4],
        arrow.get("2026-09-01").floor("month"),
        allow_shortfall=True,
    )

    assert len(slots) == 9
    assert [sequence_no for _, _, sequence_no, _ in slots] == list(range(1, 10))
    assert all(total_count == 9 for _, _, _, total_count in slots)
    assert [scheduled_date for scheduled_date, *_ in slots] == [
        day.date()
        for day in arrow.Arrow.range(
            "day", arrow.get("2026-09-01"), arrow.get("2026-09-30")
        )
        if day.weekday() in {1, 4}
    ]


def test_generate_monthly_slots_still_rejects_zero_publishable_days():
    with pytest.raises(ValueError, match="발행 가능한 날짜가 없습니다"):
        generate_monthly_slots(
            "PLAN_12",
            [],
            arrow.get("2026-09-01").floor("month"),
            allow_shortfall=True,
        )


def test_allocate_stacked_dates_pins_nowon_august_nine_slot_distribution():
    dates = [date(2026, 8, 26) + timedelta(days=offset) for offset in range(6)]

    allocated = allocate_stacked_dates(dates, 9)

    assert allocated == [
        date(2026, 8, 26),
        date(2026, 8, 26),
        date(2026, 8, 27),
        date(2026, 8, 28),
        date(2026, 8, 28),
        date(2026, 8, 29),
        date(2026, 8, 30),
        date(2026, 8, 31),
        date(2026, 8, 31),
    ]
    counts = Counter(allocated)
    assert len(allocated) == 9
    assert set(allocated) == set(dates)
    assert max(counts.values()) == 2
    assert {day.day for day, count in counts.items() if count == 2} == {26, 28, 31}


def test_allocate_stacked_dates_is_deterministic():
    dates = [date(2026, 8, 26) + timedelta(days=offset) for offset in range(6)]

    assert allocate_stacked_dates(dates, 9) == allocate_stacked_dates(dates, 9)


def test_allocate_stacked_dates_rejects_more_than_daily_capacity():
    dates = [date(2026, 8, 26) + timedelta(days=offset) for offset in range(6)]

    with pytest.raises(ValueError, match="최대 배치 수"):
        allocate_stacked_dates(dates, 13, max_per_day=2)
