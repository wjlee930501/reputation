"""Essence가 승인된 병원의 첫 콘텐츠 일정을 계약 요금제와 기본 요일로 만든다.

사람 경로(`POST /admin/hospitals/{id}/schedule`)는 일정 *교체*까지 다루는 async 경로다.
여기는 아직 일정이 한 번도 없던 병원의 *첫* 일정만 만든다 — 교체·재배치가 없으므로
슬롯 생성은 월말 배치와 같은 `create_next_month_slots_for_schedule`을 쓰고, 감사행은
사람 경로와 같은 action·detail 키로 남긴다. 사람은 언제든 콘텐츠 화면에서 교체할 수 있다.
"""

from __future__ import annotations

from datetime import date, timedelta

import arrow

from app.services.content_calendar import generate_monthly_slots

#: admin/lib/schedule.ts `DEFAULT_PUBLISH_DAYS_BY_PLAN`과 같은 값(월=0 … 일=6).
#: 요일 수 × 4주가 정확히 계약 편수라 28일짜리 달도 엄격 배치가 된다.
DEFAULT_PUBLISH_DAYS_BY_PLAN: dict[str, tuple[int, ...]] = {
    "PLAN_20": (0, 1, 2, 3, 4),
    "PLAN_16": (0, 1, 2, 3),
    "PLAN_12": (0, 2, 4),
}


def initial_schedule_terms(
    plan: str,
    *,
    today: date,
    contract_effective_on: date | None,
) -> tuple[list[int], date]:
    """첫 일정의 (발행 요일, 시작일).

    시작일은 내일과 계약 효력일 중 늦은 날이다. 그 달 남은 기본 요일로 계약 편수를 엄격하게
    배치할 수 없으면(사람 경로의 첫 일정 검사와 같은 규칙) 다음 달 1일로 미룬다 — 남은
    며칠에 한 달 편수를 몰아 넣지 않는다.
    """

    days = list(DEFAULT_PUBLISH_DAYS_BY_PLAN[plan])
    earliest = today + timedelta(days=1)
    if contract_effective_on is not None and contract_effective_on > earliest:
        earliest = contract_effective_on
    month = arrow.get(earliest).floor("month")
    try:
        generate_monthly_slots(plan, days, month, start_date=earliest)
    except ValueError:
        return days, month.shift(months=1).date()
    return days, earliest
