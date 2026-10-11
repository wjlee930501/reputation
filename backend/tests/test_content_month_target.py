"""첫 계약 월 안분 규칙과 월 목표 편수 판정(순수 함수)."""

from datetime import date

import pytest

from app.services.content_month_target import (
    prorated_first_month_quota,
    resolve_month_target,
)


def test_mid_month_start_prorates_by_remaining_publish_weekdays() -> None:
    # 2026-10 월·수·금은 13일, 10/12부터 남은 날은 9일 → ceil(9/13×12) = 9편.
    assert prorated_first_month_quota("PLAN_12", [0, 2, 4], date(2026, 10, 12)) == 9
    # 2026-10 월~목은 17일, 10/26부터 남은 날은 4일 → ceil(4/17×16) = 4편.
    assert prorated_first_month_quota("PLAN_16", [0, 1, 2, 3], date(2026, 10, 26)) == 4


def test_first_of_month_start_keeps_the_full_quota() -> None:
    assert prorated_first_month_quota("PLAN_20", [0, 1, 2, 3, 4], date(2026, 11, 1)) == 20
    assert prorated_first_month_quota("PLAN_12", [0, 2, 4], date(2026, 10, 1)) == 12


def test_proration_is_at_least_one_while_a_publish_weekday_remains() -> None:
    # 10/30(금) 시작이면 남은 월·수·금은 하루 → ceil(1/13×12) = 1편.
    assert prorated_first_month_quota("PLAN_12", [0, 2, 4], date(2026, 10, 30)) == 1


def test_no_remaining_publish_weekday_means_no_first_month_slots() -> None:
    # 10/31(토) 시작이면 그 달에 남은 게시 요일이 없다 — 첫 달 약속은 0편, 다음 달부터 시작한다.
    assert prorated_first_month_quota("PLAN_12", [0, 2, 4], date(2026, 10, 31)) == 0


def test_unknown_plan_has_no_quota() -> None:
    assert prorated_first_month_quota("PLAN_99", [0], date(2026, 10, 12)) is None


@pytest.mark.parametrize(
    ("quota", "first_month_recorded_total", "expected"),
    [
        (12, None, 12),  # 첫 달이 아니거나 아직 슬롯이 없다 → 요금제 편수
        (12, 9, 9),  # 첫 달 슬롯이 안분 편수를 기록했다 → 그 편수
        (12, 12, 12),  # 사람이 만든 첫 달(요금제 편수 그대로) → 소급하지 않는다
        (12, 20, 12),  # 다른 요금제의 큰 값은 계약을 늘리지 않는다
        (None, 9, None),  # 요금제를 모르면 목표도 모른다
    ],
)
def test_month_target_uses_a_smaller_recorded_first_month_total(
    quota, first_month_recorded_total, expected
) -> None:
    assert resolve_month_target(quota, first_month_recorded_total) == expected
