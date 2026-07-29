"""격주 측정 주차 판정 — ISO 53주 연도 경계에서도 간격이 끊기지 않아야 한다.

`isocalendar()[1] % 2`는 연도 경계에서 패리티 연속성이 깨진다. 2026년은 ISO 53주
연도라 52주(짝) → 53주(홀) → 1주(홀)로 이어져 NORMAL 우선순위 쿼리가 3주를 건너뛴다.
그 결과 12월·1월 표본이 절반이 되어 월간 리포트의 전월 대비 변화가 실제 변화가 아니라
표본 수 변화로 오염된다.
"""
from datetime import date, timedelta

from app.workers.tasks import _is_even_measurement_week


def test_measurement_weeks_alternate_without_gaps_across_a_53_week_year():
    """2026-12 ~ 2027-01 구간에서 측정 주가 정확히 격주로 유지된다."""
    mondays = [date(2026, 12, 21) + timedelta(weeks=i) for i in range(6)]
    flags = [_is_even_measurement_week(d) for d in mondays]

    # 연속 두 주가 같은 판정이면 간격이 깨진 것이다.
    for earlier, later in zip(flags, flags[1:]):
        assert earlier != later, f"격주 간격이 끊겼다: {list(zip(mondays, flags))}"


def test_the_naive_iso_parity_would_have_skipped_three_weeks_in_a_row():
    """회귀 근거 고정 — 옛 방식은 실제로 연속 스킵을 만든다."""
    dec28, jan04 = date(2026, 12, 28), date(2027, 1, 4)

    # 옛 방식: 두 주 모두 홀수 → 연속 스킵
    assert dec28.isocalendar()[1] % 2 == 1
    assert jan04.isocalendar()[1] % 2 == 1

    # 새 방식: 두 주의 판정이 갈린다
    assert _is_even_measurement_week(dec28) != _is_even_measurement_week(jan04)


def test_parity_is_stable_within_a_week():
    """같은 주의 어느 요일에 실행돼도 판정이 같아야 한다(beat 지연·재시도 대비)."""
    monday = date(2026, 6, 1)
    flags = {_is_even_measurement_week(monday + timedelta(days=i)) for i in range(7)}
    assert len(flags) == 1
