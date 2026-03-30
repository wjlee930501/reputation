"""콘텐츠 캘린더 생성 — 단일 소스"""
import logging
from datetime import date

import arrow

from app.models.content import ContentType, PLAN_DISTRIBUTION

logger = logging.getLogger(__name__)


def generate_monthly_slots(
    plan: str,
    publish_days: list[int],
    target_month: arrow.Arrow,
) -> list[tuple[date, ContentType, int, int]]:
    """
    해당 월의 발행 날짜·유형·순번·총편수 목록 생성.
    Returns: [(scheduled_date, content_type, seq_no, total_count), ...]
    """
    distribution = PLAN_DISTRIBUTION.get(plan, {})
    type_sequence: list[ContentType] = []
    for ctype, count in distribution.items():
        type_sequence.extend([ctype] * count)
    total = len(type_sequence)

    dates: list[date] = []
    day = target_month.floor("month")
    end = target_month.ceil("month")
    while day <= end:
        if day.weekday() in publish_days:
            dates.append(day.date())
        day = day.shift(days=1)

    if len(dates) < total:
        raise ValueError(
            f"발행일 수({len(dates)})가 요금제 편수({total})보다 적습니다. "
            f"발행 요일을 추가하거나 요금제를 변경해 주세요. "
            f"({target_month.format('YYYY-MM')})"
        )

    result = [
        (pub_date, ctype, i + 1, total)
        for i, (pub_date, ctype) in enumerate(zip(dates, type_sequence))
    ]

    return result
