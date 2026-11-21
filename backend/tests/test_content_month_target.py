"""첫 계약 월 안분 규칙과 월 목표 편수 판정(순수 함수)."""

from datetime import date

import arrow
import pytest

from app.services.content_month_target import (
    prorated_first_month_quota,
    recorded_first_month_target,
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
    ("quota", "first_month_target", "expected"),
    [
        (12, None, 12),  # 첫 달이 아니거나 사람이 만든 첫 일정 → 요금제 편수
        (12, 9, 9),  # 시스템이 안분한 첫 달 → 그 편수
        (12, 0, 0),  # 시작일 뒤로 게시 요일이 남지 않은 첫 달 → 0편
        (12, 12, 12),  # 1일 시작(안분 없음)
        (12, 20, 12),  # 다른 요금제의 큰 값은 계약을 늘리지 않는다
        (None, 9, None),  # 요금제를 모르면 목표도 모른다
    ],
)
def test_month_target_uses_a_smaller_recorded_first_month_target(
    quota, first_month_target, expected
) -> None:
    assert resolve_month_target(quota, first_month_target) == expected


@pytest.mark.parametrize(
    "quota,first_month_target,allocated,expected",
    [
        (12, 7, 12, 12),  # 안분 뒤 첫 달 배정을 늘렸으면 그 배정이 약속이다
        (12, 7, 5, 7),  # 배정이 모자라면 기록한 안분 편수가 약속이다(미이행이 보인다)
        (12, 7, 13, 12),  # 요금제 편수를 넘지 않는다
        (12, None, 20, 12),  # 안분하지 않은 달은 배정과 무관하게 요금제 편수
    ],
)
def test_prorated_month_target_follows_a_larger_allocation(
    quota, first_month_target, allocated, expected
) -> None:
    assert resolve_month_target(quota, first_month_target, allocated) == expected


def test_recorded_first_month_target_applies_only_to_the_recorded_start_month() -> None:
    october, november = arrow.get(date(2026, 10, 1)), arrow.get(date(2026, 11, 1))
    detail = {"active_from": "2026-10-12", "first_month_target": 9}
    assert recorded_first_month_target(detail, october) == 9
    assert recorded_first_month_target(detail, november) is None
    zero = {"active_from": "2026-10-31", "first_month_target": 0}
    assert recorded_first_month_target(zero, october) == 0
    # 근거가 없거나 깨진 기록은 안분으로 읽지 않는다(요금제 편수 그대로).
    for broken in (
        None,
        {},
        {"active_from": "2026-10-12"},
        {"first_month_target": 9},
        {"active_from": "not-a-date", "first_month_target": 9},
        {"active_from": "2026-10-12", "first_month_target": True},
        {"active_from": "2026-10-12", "first_month_target": -1},
    ):
        assert recorded_first_month_target(broken, october) is None



# ── 마케터가 정한 첫 달 편수(2026-11-21 마케팅 요청 #8) ─────────────────────────
def test_last_marker_for_the_month_wins_between_contract_and_operator_targets():
    import arrow as _arrow

    from app.services.content_month_target import (
        last_month_marker,
        month_quota_restored,
        operator_cancelled_ids,
        operator_month_target,
    )

    nov = _arrow.get("2026-11-01")
    contract = {"contract_quota_month": "2026-11"}
    five = {"operator_month_target": {"month": "2026-11", "target": 5, "cancelled_ids": ["a", "b"]}}
    other_month = {"operator_month_target": {"month": "2026-12", "target": 3}}

    assert last_month_marker([], nov) is None
    assert last_month_marker([(1, contract), (2, five)], nov) == ("operator", 5)
    assert operator_month_target([(1, contract), (2, five)], nov) == 5
    assert month_quota_restored([(1, contract), (2, five)], nov) is False
    # 나중에 계약 편수로 되돌렸으면 그 표지가 이긴다.
    assert operator_month_target([(1, five), (2, contract)], nov) is None
    assert month_quota_restored([(1, five), (2, contract)], nov) is True
    # 다른 달의 표지는 이 달과 무관하다.
    assert last_month_marker([(1, other_month)], nov) is None
    # 이 기능이 종료한 슬롯은 되돌린 뒤에도 기억한다(다시 늘릴 때만 쓴다).
    assert operator_cancelled_ids([(1, five), (2, contract)], nov) == {"a", "b"}
    # 모양이 틀린 표지는 읽지 않는다.
    broken = {"operator_month_target": {"month": "2026-11", "target": True}}
    assert last_month_marker([(1, broken)], nov) is None


def test_operator_target_is_capped_by_quota_and_raised_by_live_slots():
    from app.services.content_month_target import resolve_month_target

    assert resolve_month_target(12, None, 5, operator_target=5) == 5
    assert resolve_month_target(12, None, 0, operator_target=0) == 0
    # 사람이 남긴 원고가 더 많으면 그 수다(안분과 같은 규칙).
    assert resolve_month_target(12, None, 7, operator_target=5) == 7
    assert resolve_month_target(12, None, 30, operator_target=5) == 12
    # 미룬 달 판정이 먼저다.
    assert resolve_month_target(12, None, 2, voided=True, operator_target=5) == 2
