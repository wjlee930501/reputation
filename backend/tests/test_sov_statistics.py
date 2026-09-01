"""Wilson 구간·유의성 판정 — 알려진 값으로 산식을 고정한다.

이 숫자들이 틀리면 원장 리포트가 "의미 있는 상승"이라고 말하는 근거가 통째로
틀린다. 교과서 값(Wilson score interval, z=1.96)과 대조한다.
"""

import pytest

from app.services.sov_statistics import (
    delta_significance,
    wilson_interval,
)


def _rounded(successes: int, trials: int) -> tuple[float, float]:
    interval = wilson_interval(successes, trials)
    assert interval is not None
    return round(interval.low, 4), round(interval.high, 4)


def test_wilson_matches_published_values():
    # 5/10: 널리 인용되는 0.2366~0.7634
    assert _rounded(5, 10) == (0.2366, 0.7634)
    # 0/10: Wald는 폭 0으로 붕괴하지만 Wilson은 0~0.2775
    assert _rounded(0, 10) == (0.0, 0.2775)
    # 10/10: 대칭
    assert _rounded(10, 10) == (0.7225, 1.0)
    # 1/20, 3/7 — 작은 표본의 비대칭성이 유지된다
    assert _rounded(1, 20) == (0.0089, 0.2361)
    assert _rounded(3, 7) == (0.1582, 0.7495)
    # 47/150 — 월간 헤드라인 규모(질문 15 × 서비스 2 × 반복 5)의 실제 표본
    assert _rounded(47, 150) == (0.2445, 0.3914)


def test_zero_and_full_counts_never_claim_zero_uncertainty():
    zero = wilson_interval(0, 150)
    full = wilson_interval(150, 150)
    assert zero is not None and full is not None
    assert zero.high > 0
    assert full.low < 1


def test_no_trials_yields_no_interval_instead_of_a_fabricated_one():
    assert wilson_interval(0, 0) is None
    assert delta_significance(0, 0, 0, 0) is None
    assert delta_significance(5, 10, 0, 0) is None


def test_successes_above_trials_is_a_programming_error():
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_point_estimate_and_margin_are_reported_in_hundred_units():
    interval = wilson_interval(47, 150)
    assert interval is not None
    assert interval.point_pct == 31.33
    # 실효 표본 150에서도 ±7번 — 예전 고정 상수 ±5번보다 넓다.
    # (셀당 1건만 쓰던 표본 30에서는 아래처럼 ±16번이었다)
    assert interval.margin_of_hundred == 7
    thin = wilson_interval(9, 30)
    assert thin is not None and thin.margin_of_hundred == 16


def test_thin_samples_are_never_significant():
    """셀당 1회 측정 시절 표본(30)에서 30%→40%는 판정 불가여야 한다."""
    assert delta_significance(12, 30, 9, 30) == "WITHIN_NOISE"


def test_a_ten_point_move_on_150_trials_is_still_noise():
    assert delta_significance(75, 150, 60, 150) == "WITHIN_NOISE"


def test_a_large_move_is_significant_in_both_directions():
    assert delta_significance(120, 150, 30, 150) == "SIGNIFICANT_UP"
    assert delta_significance(30, 150, 120, 150) == "SIGNIFICANT_DOWN"


def test_verdict_is_conservative_relative_to_a_two_proportion_z_test():
    """비중첩 판정은 z-검정보다 좁게 기각한다 — 낙관 편향을 상쇄하는 선택이다."""
    # 이 표본은 두 비율 z-검정에서는 p<0.05지만, 구간이 겹쳐 노이즈로 남는다.
    assert delta_significance(90, 150, 70, 150) == "WITHIN_NOISE"
