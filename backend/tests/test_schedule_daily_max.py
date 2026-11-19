"""하루 최대 편수(일정 `daily_max`)와 사람 저장의 계약 편수 표지 — 순수 함수 단위."""

from datetime import date
from types import SimpleNamespace

import arrow
import pytest

from app.models.content import ContentStatus
from app.services.content_calendar import (
    capped_publish_dates,
    daily_capacity,
    generate_monthly_slots,
)
from app.services.content_month_target import (
    CONTRACT_QUOTA_MONTH_KEY,
    month_quota_restored,
    resolve_month_target,
)
from app.services.schedule_reconciliation import allocation_stays, place_open_slots

NOV = arrow.get(2026, 11, 1)


def _days(slots):
    return [slot[0] for slot in slots]


def test_daily_max_spreads_one_a_day_before_doubling():
    slots = generate_monthly_slots("PLAN_12", list(range(7)), NOV, date(2026, 11, 21), daily_max=2)
    days = _days(slots)
    assert len(days) == 12 and min(days) == date(2026, 11, 21)
    counts = {day: days.count(day) for day in days}
    assert len(counts) == 10 and sorted(counts.values()) == [1] * 8 + [2] * 2


def test_daily_max_one_keeps_one_a_day_and_rejects_shortfall():
    with pytest.raises(ValueError, match="발행일 수\\(10\\)"):
        generate_monthly_slots("PLAN_12", list(range(7)), NOV, date(2026, 11, 21))


def test_daily_max_shortfall_message_names_the_cap():
    with pytest.raises(ValueError, match="하루 최대 2편 = 8편"):
        generate_monthly_slots("PLAN_12", [0, 2, 4], NOV, date(2026, 11, 23), daily_max=2)


def test_daily_max_out_of_range_is_rejected():
    with pytest.raises(ValueError, match="1~3"):
        generate_monthly_slots("PLAN_12", list(range(7)), NOV, daily_max=4)


def test_ensure_quota_fills_chosen_weekdays_to_the_cap_before_other_days():
    # 11/25부터 월·수·금은 3일(25·27·30) — 하루 2편까지 선택 요일을 먼저 채우고 나머지는 다른 날이다.
    slots = generate_monthly_slots(
        "PLAN_12", [0, 2, 4], NOV, date(2026, 11, 25), ensure_quota=True, daily_max=2
    )
    days = _days(slots)
    chosen = [day for day in days if day.weekday() in (0, 2, 4)]
    assert len(days) == 12 and len(chosen) == 6
    assert all(days.count(day) <= 2 for day in set(days))


def test_capped_dates_respects_already_occupied_days():
    window = [date(2026, 11, d) for d in range(21, 31)]
    occupied = {date(2026, 11, 21): 1, date(2026, 11, 22): 2}
    assert daily_capacity(window, 2, occupied) == 20 - 3
    placed = capped_publish_dates(window, 12, 2, occupied)
    assert date(2026, 11, 22) not in placed
    assert placed.count(date(2026, 11, 21)) <= 1


def _slot(seq, day):
    return SimpleNamespace(sequence_no=seq, scheduled_date=day, total_count=12)


def _row(seq, day, **extra):
    fields = {
        "sequence_no": seq,
        "scheduled_date": day,
        "status": ContentStatus.DRAFT,
        "carried_over_from": None,
        "first_published_at": None,
        "published_at": None,
        "human_edited_at": None,
        "generation_claim_token": None,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def test_place_open_slots_keeps_published_rows_and_caps_the_rest():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Plan:
        sequence_no: int
        scheduled_date: date
        total_count: int = 12

    today = date(2026, 11, 20)
    window = [date(2026, 11, d) for d in range(21, 31)]
    planned = [Plan(seq, date(2026, 11, 21)) for seq in range(1, 13)]
    existing = [
        _row(1, date(2026, 11, 20), status=ContentStatus.PUBLISHED),  # 지난·공개 — 그대로
        _row(2, date(2026, 11, 22), human_edited_at=today),  # 사람이 고친 원고 — 그대로
        _row(3, date(2026, 11, 25)),  # 옮길 수 있다
    ]
    assert allocation_stays(existing[0], today=today)
    assert allocation_stays(existing[1], today=today)
    assert not allocation_stays(existing[2], today=today)

    placed, unplaced = place_open_slots(
        planned, existing, today=today, window_dates=window, daily_max=1
    )
    assert unplaced == 1  # 10일 중 11/22는 이미 1편 — 하루 1편으로 10편 놓을 자리가 9뿐
    placed, unplaced = place_open_slots(
        planned, existing, today=today, window_dates=window, daily_max=2
    )
    assert unplaced == 0
    by_seq = {slot.sequence_no: slot.scheduled_date for slot in placed}
    assert by_seq[1] == date(2026, 11, 21) and by_seq[2] == date(2026, 11, 21)  # 계획 그대로
    moved = [by_seq[seq] for seq in range(3, 13)]
    counts = {day: moved.count(day) for day in moved}
    counts[date(2026, 11, 22)] = counts.get(date(2026, 11, 22), 0) + 1
    assert max(counts.values()) <= 2


def test_human_save_marker_restores_only_its_month():
    records = [
        (None, {"active_from": "2026-11-20", "first_month_target": 5}),
        (None, {"active_from": "2026-11-21", CONTRACT_QUOTA_MONTH_KEY: "2026-11"}),
    ]
    assert month_quota_restored(records, NOV)
    assert not month_quota_restored(records, arrow.get(2026, 12, 1))
    # 표지 없는 옛 사람 저장은 소급하지 않는다.
    assert not month_quota_restored(records[:1], NOV)
    assert resolve_month_target(12, 5, 5, quota_restored=True) == 12
    assert resolve_month_target(12, 5, 5) == 5
    # 미룬 달 판정이 먼저다.
    assert resolve_month_target(12, 5, 0, voided=True, quota_restored=True) == 0


def test_place_open_slots_judges_rows_not_sequence_numbers():
    """취소 행과 살아 있는 행이 같은 순번이어도 살아 있는 행은 옮길 슬롯으로 센다(Codex 검토)."""
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Plan:
        sequence_no: int
        scheduled_date: date
        total_count: int = 12

    today = date(2026, 11, 20)
    window = [date(2026, 11, d) for d in range(21, 31)]
    planned = [Plan(seq, date(2026, 11, 21)) for seq in range(1, 13)]
    existing = [_row(seq, date(2026, 11, 2), status=ContentStatus.CANCELLED) for seq in range(1, 9)]
    existing += [_row(seq, date(2026, 11, 25)) for seq in range(1, 13)]
    # 살아 있는 12편을 10일에 하루 1편으로는 놓을 수 없다.
    _placed, unplaced = place_open_slots(
        planned, existing, today=today, window_dates=window, daily_max=1
    )
    assert unplaced == 2
    placed, unplaced = place_open_slots(
        planned, existing, today=today, window_dates=window, daily_max=2
    )
    days = [slot.scheduled_date for slot in placed]
    assert unplaced == 0 and max(days.count(day) for day in set(days)) <= 2


def test_place_open_slots_counts_articles_carried_in_from_another_month():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Plan:
        sequence_no: int
        scheduled_date: date
        total_count: int = 10

    window = [date(2026, 11, d) for d in range(21, 31)]
    planned = [Plan(seq, date(2026, 11, 21)) for seq in range(1, 11)]
    _placed, unplaced = place_open_slots(
        planned, [], today=date(2026, 11, 20), window_dates=window, daily_max=1,
        other_dates=[date(2026, 11, 21)],
    )
    assert unplaced == 1


def test_duplicate_live_sequence_moves_once_and_the_other_holds_its_day():
    """옛 일정이 둘이라 살아 있는 같은 순번 행이 둘이면 하나만 옮기고 나머지는 그날 자리를 쓴다."""
    from dataclasses import dataclass

    from app.services.schedule_reconciliation import movable_rows

    @dataclass(frozen=True)
    class Plan:
        sequence_no: int
        scheduled_date: date
        total_count: int = 12

    today = date(2026, 11, 19)
    window = [date(2026, 11, d) for d in range(20, 31)]  # 11일
    planned = [Plan(seq, date(2026, 11, 20)) for seq in range(1, 13)]
    existing = [_row(1, date(2026, 11, 25))]
    existing += [_row(seq, date(2026, 11, 26)) for seq in range(1, 12)]
    assert len(movable_rows(existing, today=today)) == 11
    _placed, unplaced = place_open_slots(
        planned, existing, today=today, window_dates=window, daily_max=1
    )
    # 11일 중 하루는 옮기지 않는 중복 행이 쓰므로 하루 1편으로는 11편을 놓을 수 없다.
    assert unplaced == 1
