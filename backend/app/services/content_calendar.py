"""콘텐츠 캘린더 생성 — 단일 소스"""
import hashlib
import logging
from datetime import date

import arrow

from app.models.content import PLAN_DISTRIBUTION, ContentType

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
    따라서 월~일을 모두 허용한 PLAN_12도 1~12일 연속 발행하지 않고 월초~월말에
    걸쳐 12번 발행해야 한다. 같은 입력에 항상 같은 결과를 내며, 첫/마지막 가능한
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


#: 발행 요일 하루에 놓을 수 있는 최대 편수의 상한(일정 `daily_max`, DB 검사 제약과 같다).
MAX_DAILY_ARTICLES = 3


def capped_publish_dates(
    dates: list[date],
    total: int,
    daily_max: int,
    occupied: dict[date, int] | None = None,
) -> list[date]:
    """`total`편을 `dates`에 하루 `daily_max`편 이하로 놓는다(이미 놓인 편수 `occupied` 포함).

    하루 1편씩 월 전체에 고르게 깐 뒤, 남은 편수만 2편째로 다시 고르게 얹는다(3편째도 같다).
    그래서 하루 최대 2편이어도 날짜가 충분하면 하루 1편이고, 몰리는 날은 고르게 흩어진다.
    자리가 모자라면 놓을 수 있는 만큼만 돌려준다 — 호출부가 `daily_capacity`로 먼저 확인한다.
    """
    occupied = occupied or {}
    picked: list[date] = []
    remaining = total
    for level in range(1, daily_max + 1):
        if remaining <= 0:
            break
        eligible = [day for day in dates if occupied.get(day, 0) < level]
        take = min(remaining, len(eligible))
        picked.extend(_spread_dates(eligible, take))
        remaining -= take
    return sorted(picked)


def daily_capacity(
    dates: list[date], daily_max: int, occupied: dict[date, int] | None = None
) -> int:
    """`dates`에 하루 `daily_max`편까지 더 놓을 수 있는 편수."""
    occupied = occupied or {}
    return sum(max(0, daily_max - occupied.get(day, 0)) for day in dates)


def preferred_publish_dates(
    publish_days: list[int], target_month: arrow.Arrow, start_date: date | None
) -> list[date]:
    """그 달에서 시작일 이후의 발행 요일 날짜."""
    return [
        day.date()
        for day in arrow.Arrow.range(
            "day", target_month.floor("month"), target_month.ceil("month")
        )
        if day.weekday() in publish_days and (start_date is None or day.date() >= start_date)
    ]


def generate_monthly_slots(
    plan: str,
    publish_days: list[int],
    target_month: arrow.Arrow,
    start_date: date | None = None,
    hospital_id: object | None = None,
    ensure_quota: bool = False,
    total: int | None = None,
    daily_max: int = 1,
) -> list[tuple[date, ContentType, int, int]]:
    """
    해당 월의 발행 날짜·유형·순번·총편수 목록 생성.
    Returns: [(scheduled_date, content_type, seq_no, total_count), ...]

    hospital_id가 주어지면 병원별로 시작 유형이 달라지는 결정적 인터리빙을 적용한다.
    생략 시 plan+target_month를 시드로 사용(호출부 변경 없이도 유형 몰림은 해소됨).

    `total`은 그 달 약속 편수가 요금제 편수보다 작을 때(첫 계약 월 안분,
    `services/content_month_target`)만 준다. 고르게 섞인 유형 순서의 앞부분을 쓴다.

    `daily_max`는 발행 요일 하루에 놓을 수 있는 최대 편수(일정의 `daily_max`, 1~3)다.
    하루 1편씩 고르게 깐 뒤 모자랄 때만 2편째(그다음 3편째)를 고르게 얹는다. 엄격 배치
    (`ensure_quota=False`)는 `발행 요일 수 × daily_max`가 편수보다 적으면 거절한다.
    """
    distribution = PLAN_DISTRIBUTION.get(plan, {})
    seed = f"{hospital_id if hospital_id is not None else plan}:{target_month.format('YYYY-MM')}"
    type_sequence: list[ContentType] = _interleave_types(distribution, seed)
    if total is not None:
        type_sequence = type_sequence[: max(0, total)]
    total = len(type_sequence)

    dates = preferred_publish_dates(publish_days, target_month, start_date)
    end = target_month.ceil("month")

    if not publish_days or any(type(day) is not int or not 0 <= day <= 6 for day in publish_days):
        raise ValueError("발행 가능한 날짜가 없습니다. 발행 요일은 0~6 사이의 값으로 하나 이상 설정해야 합니다.")
    remaining_dates = [
        day.date()
        for day in arrow.Arrow.range("day", target_month.floor("month"), end)
        if start_date is None or day.date() >= start_date
    ]
    # A valid contract repair may have no preferred weekday left this month.
    # The remaining calendar days still belong to the contract; a genuinely empty
    # month/start-date window does not. Strict initial scheduling is unchanged.
    if not dates and (not ensure_quota or not remaining_dates):
        raise ValueError(
            "발행 가능한 날짜가 없습니다. "
            f"발행 요일과 시작일을 확인해 주세요. ({target_month.format('YYYY-MM')})"
        )

    if type(daily_max) is not int or not 1 <= daily_max <= MAX_DAILY_ARTICLES:
        raise ValueError(f"하루 최대 편수는 1~{MAX_DAILY_ARTICLES} 사이여야 합니다.")
    capacity = len(dates) * daily_max
    if capacity < total and not ensure_quota:
        if daily_max == 1:
            raise ValueError(
                f"발행일 수({len(dates)})가 요금제 편수({total})보다 적습니다. "
                f"발행 요일을 추가하거나 하루 최대 편수를 늘려 주세요. "
                f"({target_month.format('YYYY-MM')})"
            )
        raise ValueError(
            f"발행일 수({len(dates)}) × 하루 최대 {daily_max}편 = {capacity}편으로 "
            f"이번 달 편수({total})를 채울 수 없습니다. "
            f"발행 요일을 추가하거나 하루 최대 편수를 늘려 주세요. "
            f"({target_month.format('YYYY-MM')})"
        )
    if capacity >= total:
        selected_dates = capped_publish_dates(dates, total, daily_max)
    else:
        # Weekdays are preferences; the contract remains 12/16/20 even in short
        # months. Fill the chosen weekdays up to the daily cap, then off-weekday
        # gaps (also capped). A late start may still require more than the cap
        # per day, but must never silently reduce the purchased quota.
        other_dates = [day for day in remaining_dates if day not in dates]
        pool = sorted(dates * daily_max)
        needed = total - len(pool)
        pool = sorted(
            pool
            + capped_publish_dates(other_dates, min(needed, len(other_dates) * daily_max), daily_max)
        )
        if len(pool) < total:
            pool = [pool[i * len(pool) // total] for i in range(total)]
        selected_dates = pool
    result = [
        (pub_date, ctype, i + 1, total)
        for i, (pub_date, ctype) in enumerate(zip(selected_dates, type_sequence))
    ]

    return result
