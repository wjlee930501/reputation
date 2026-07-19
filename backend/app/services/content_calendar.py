"""콘텐츠 캘린더 생성 — 단일 소스"""
import hashlib
import logging
from datetime import date

import arrow

from app.models.content import ContentType, PLAN_DISTRIBUTION

logger = logging.getLogger(__name__)


def _interleave_types(distribution: dict, seed: str) -> list:
    """유형별 개수를 몰아주지 않고 고르게 분산한 결정적 순서로 배치.

    dict 순서대로 extend하면 개수가 많은 유형(FAQ 등)이 앞쪽에 연속 배정된다
    (예: PLAN_16의 첫 4회가 전부 FAQ). 각 유형의 등장을 "그 유형 내에서의
    상대 위치(occurrence_index / count)"로 정렬해 균등하게 흩뿌린 뒤, seed로
    결정적 회전을 적용해 같은 배분이라도 병원/연월마다 시작 유형이 달라지게 한다.
    같은 (distribution, seed) 입력은 항상 같은 순서를 반환한다(결정론 — 테스트 가능).
    """
    items: list[tuple[float, int, ContentType]] = []
    for order_idx, (ctype, count) in enumerate(distribution.items()):
        if count <= 0:
            continue
        for i in range(count):
            items.append((i / count, order_idx, ctype))
    items.sort(key=lambda entry: (entry[0], entry[1]))
    sequence = [ctype for _, _, ctype in items]

    if not sequence:
        return sequence

    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    rotate_by = int(digest, 16) % len(sequence)
    return sequence[rotate_by:] + sequence[:rotate_by]


def _spread_dates(dates: list[date], total: int) -> list[date]:
    """월의 앞부분에 콘텐츠가 몰리지 않도록 가능한 날짜 전체에 고르게 배치한다.

    ``publish_days``는 "발행 가능한 요일"이고 실제 월간 편수는 plan이 결정한다.
    따라서 월~일을 모두 허용한 PLAN_8도 1~8일 연속 발행하지 않고 월초~월말에
    걸쳐 8번 발행해야 한다. 같은 입력에 항상 같은 결과를 내며, 첫/마지막 가능한
    날짜를 포함해 월 전체를 활용한다.
    """
    if total <= 0:
        return []
    if len(dates) < total:
        raise ValueError("발행 가능한 날짜가 월간 콘텐츠 편수보다 적습니다.")
    if total == 1:
        return [dates[len(dates) // 2]]
    if len(dates) == total:
        return dates

    last_index = len(dates) - 1
    # len(dates) >= total이므로 인접 지점 간 간격은 1 이상이고 index가 중복되지 않는다.
    indexes = [round(i * last_index / (total - 1)) for i in range(total)]
    return [dates[index] for index in indexes]


def generate_monthly_slots(
    plan: str,
    publish_days: list[int],
    target_month: arrow.Arrow,
    start_date: date | None = None,
    hospital_id: object | None = None,
) -> list[tuple[date, ContentType, int, int]]:
    """
    해당 월의 발행 날짜·유형·순번·총편수 목록 생성.
    Returns: [(scheduled_date, content_type, seq_no, total_count), ...]

    hospital_id가 주어지면 병원별로 시작 유형이 달라지는 결정적 인터리빙을 적용한다.
    생략 시 plan+target_month를 시드로 사용(호출부 변경 없이도 유형 몰림은 해소됨).
    """
    distribution = PLAN_DISTRIBUTION.get(plan, {})
    seed = f"{hospital_id if hospital_id is not None else plan}:{target_month.format('YYYY-MM')}"
    type_sequence: list[ContentType] = _interleave_types(distribution, seed)
    total = len(type_sequence)

    dates: list[date] = []
    day = target_month.floor("month")
    end = target_month.ceil("month")
    while day <= end:
        day_date = day.date()
        if day.weekday() in publish_days and (start_date is None or day_date >= start_date):
            dates.append(day_date)
        day = day.shift(days=1)

    if len(dates) < total:
        raise ValueError(
            f"발행일 수({len(dates)})가 요금제 편수({total})보다 적습니다. "
            f"발행 요일을 추가하거나 요금제를 변경해 주세요. "
            f"({target_month.format('YYYY-MM')})"
        )

    selected_dates = _spread_dates(dates, total)
    result = [
        (pub_date, ctype, i + 1, total)
        for i, (pub_date, ctype) in enumerate(zip(selected_dates, type_sequence))
    ]

    return result
