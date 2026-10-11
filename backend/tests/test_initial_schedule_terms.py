from datetime import date

import pytest

from app.services.initial_schedule import DEFAULT_PUBLISH_DAYS_BY_PLAN, initial_schedule_terms


def test_default_days_match_the_admin_defaults() -> None:
    # admin/lib/schedule.ts DEFAULT_PUBLISH_DAYS_BY_PLAN과 같아야 사람이 고를 기본값과 어긋나지 않는다.
    assert DEFAULT_PUBLISH_DAYS_BY_PLAN == {
        "PLAN_20": (0, 1, 2, 3, 4),
        "PLAN_16": (0, 1, 2, 3),
        "PLAN_12": (0, 2, 4),
    }


def test_start_tomorrow_when_the_rest_of_the_month_fits_the_contract() -> None:
    days, active_from = initial_schedule_terms(
        "PLAN_12", today=date(2026, 9, 30), contract_effective_on=None
    )
    assert days == [0, 2, 4]
    assert active_from == date(2026, 10, 1)


def test_mid_month_start_begins_tomorrow() -> None:
    # 첫 계약 월은 다음 달로 미루지 않고 남은 기간을 안분해 바로 시작한다(D3).
    _days, active_from = initial_schedule_terms(
        "PLAN_12", today=date(2026, 10, 11), contract_effective_on=None
    )
    assert active_from == date(2026, 10, 12)


def test_future_contract_effective_date_wins_over_tomorrow() -> None:
    _days, active_from = initial_schedule_terms(
        "PLAN_16", today=date(2026, 10, 11), contract_effective_on=date(2026, 11, 3)
    )
    assert active_from == date(2026, 11, 3)


def test_past_contract_effective_date_never_moves_the_start_backwards() -> None:
    _days, active_from = initial_schedule_terms(
        "PLAN_20", today=date(2026, 9, 30), contract_effective_on=date(2026, 9, 1)
    )
    assert active_from == date(2026, 10, 1)


def test_unknown_plan_is_rejected() -> None:
    with pytest.raises(KeyError):
        initial_schedule_terms("PLAN_99", today=date(2026, 9, 30), contract_effective_on=None)
