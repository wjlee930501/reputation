import logging
from datetime import date

import arrow
from sqlalchemy.exc import IntegrityError

from app.models.content import ContentItem, ContentSchedule, ContentStatus
from app.models.hospital import HospitalStatus
from app.services.content_brief import PLANNING_REASON_KEY
from app.services.content_calendar import generate_monthly_slots
from app.services.content_month_target import month_content_target_sync
from app.services.gap_driven_slots import (
    ExistingSlot,
    build_gap_targets,
    gap_target_rows_stmt,
    plan_gap_driven_slots,
)
from app.services.schedule_reconciliation import month_items_query, remaining_month_slots
from app.utils.db_locks import acquire_hospital_advisory_lock_sync
from app.workers.generation_attempt_state import fresh_generation_attempt

logger = logging.getLogger(__name__)


def _respect_daily_max(db, schedule, hospital_id, month, start_date, planned):
    """새로 만들 슬롯이 그 달에 이미 놓인 글과 합쳐 하루 `daily_max`편을 넘으면 날짜를 다시 놓는다.

    계획은 월 전체를 새로 세우므로, 일부 날짜가 이미 찬 달(사람 저장 뒤 당월 조정 등)에서는 남은
    순번이 찬 날에 겹칠 수 있다. 넘는 날이 없으면 계획 그대로 두고(종전 결과와 같다), 시작일 이후
    발행 요일에 자리가 충분할 때만 다시 놓는다 — 모자라면 계약을 줄이지 않고 계획 그대로 만든다.
    """
    from dataclasses import replace

    from sqlalchemy import select

    from app.services.content_calendar import (
        capped_publish_dates,
        daily_capacity,
        preferred_publish_dates,
    )

    daily_max = int(getattr(schedule, "daily_max", None) or 1)
    existing_dates = db.execute(
        select(ContentItem.scheduled_date).where(
            ContentItem.hospital_id == hospital_id,
            ContentItem.scheduled_date >= month.floor("month").date(),
            ContentItem.scheduled_date <= month.ceil("month").date(),
            ContentItem.status != ContentStatus.CANCELLED,
        )
    ).scalars().all()
    occupied: dict = {}
    for day in existing_dates:
        occupied[day] = occupied.get(day, 0) + 1
    combined = dict(occupied)
    for slot in planned:
        combined[slot.scheduled_date] = combined.get(slot.scheduled_date, 0) + 1
    if max(combined.values(), default=0) <= daily_max:
        return planned
    window = preferred_publish_dates(schedule.publish_days, month, start_date)
    if daily_capacity(window, daily_max, occupied) < len(planned):
        return planned
    ordered = sorted(planned, key=lambda slot: slot.sequence_no)
    dates = capped_publish_dates(window, len(ordered), daily_max, occupied)
    return [replace(slot, scheduled_date=day) for slot, day in zip(ordered, dates, strict=True)]


def create_next_month_slots_for_schedule(
    db,
    schedule: ContentSchedule,
    next_month: arrow.Arrow,
    next_month_start: date,
    next_month_end: date,
    *,
    not_before: date | None = None,
    month_total: int | None = None,
) -> bool:
    hospital = schedule.hospital
    if hospital.status not in (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN):
        return False
    acquire_hospital_advisory_lock_sync(db, hospital.id)

    # 스케줄 활성화 전 달은 발행 대상이 아니다. active_from이 대상 월보다 완전히 뒤면
    # (예: 7/25 배치가 8월분을 만드는데 active_from=2026-09-01) 슬롯을 만드는 순간
    # 계약보다 한 달 일찍 자동 발행이 시작된다.
    active_from = schedule.active_from
    if active_from and active_from > next_month_end:
        return False
    # 그 달 약속 편수. 첫 계약 월을 안분한 달은 첫 일정 서비스가 `month_total`로 주고,
    # 그 뒤의 재실행(매일 당월 조정)은 첫 일정 감사행이 기록한 안분 편수를 같은 판정으로
    # 읽어 요금제 편수로 다시 채우지 않는다. 시작일 뒤로 게시 요일이 없던 첫 달은 0편이다.
    month_target = (
        month_total
        if month_total is not None
        else month_content_target_sync(db, hospital.id, next_month, schedule.plan)
    )
    if month_target == 0:
        return False
    # 활성화일이 대상 월 중간에 걸리면 그 날짜 이후로만 발행해야 한다.
    start_date = active_from if active_from and active_from > next_month_start else None
    if not_before is not None:
        start_date = max(start_date or next_month_start, not_before)

    # "이 스케줄의 이번 달 계획 슬롯이 이미 만들어졌는가"로만 판정한다.
    # 월 전체에 아이템이 1건이라도 있으면 건너뛰던 과거 조건은, 지난달에서 이월된
    # (carried_over_from) 1건이나 다른 스케줄의 행 하나가 다음 달 약정 편수 전체 생성을
    # 통째로 막았다. 다른 달의 이월 슬롯은 제외하고, 이 달에서 넘어간 슬롯은 원래 계약에 포함한다.
    existing_stmt = month_items_query(hospital.id, next_month)
    existing_rows = db.execute(existing_stmt).all()
    existing_slots = [
        ExistingSlot(
            sequence_no=row.sequence_no,
            content_type=row.content_type,
            gap_driven=row.query_target_id is not None,
            query_target_id=row.query_target_id,
        )
        for row in existing_rows
    ]
    planned_total = month_target or 0
    if planned_total and len(existing_rows) >= planned_total:
        return False

    slots = generate_monthly_slots(
        schedule.plan,
        schedule.publish_days,
        next_month,
        start_date,
        ensure_quota=True,
        total=planned_total or None,
        daily_max=int(getattr(schedule, "daily_max", None) or 1),
    )

    # 측정된 미언급 격차가 유형과 대상 질문을 정하게 한다. 월 전체 슬롯을 놓고 계산해야
    # 유형 상한을 정확히 지킬 수 있으므로, 기존 순번 필터링보다 **먼저** 적용한다.
    # 이미 만들어진 슬롯은 `existing`으로 넘겨 재실행이 상한을 다시 쓰지 못하게 한다 —
    # 넘기지 않으면 1회차와 2회차 결과의 합집합이 "유형 배분의 절반" 상한을 넘는다.
    gap_targets = build_gap_targets(
        db.execute(gap_target_rows_stmt(hospital.id)).all(), hospital=hospital
    )
    planned = plan_gap_driven_slots(
        slots, plan=schedule.plan, gap_targets=gap_targets, existing=existing_slots
    )

    # 부분 생성(중단된 이전 배치 등) 뒤에는 비어 있는 순번만 채운다.
    planned = remaining_month_slots(planned, existing_rows)
    if not planned:
        return False
    planned = _respect_daily_max(db, schedule, hospital.id, next_month, start_date, planned)

    try:
        with db.begin_nested():
            for slot in planned:
                db.add(
                    ContentItem(
                        hospital_id=hospital.id,
                        schedule_id=schedule.id,
                        content_type=slot.content_type,
                        sequence_no=slot.sequence_no,
                        total_count=slot.total_count,
                        scheduled_date=slot.scheduled_date,
                        status=ContentStatus.DRAFT,
                        query_target_id=slot.query_target_id,
                        essence_check_summary={
                            "generation_attempt": fresh_generation_attempt(
                                topic_id=(
                                    str(slot.query_target_id)
                                    if slot.query_target_id
                                    else None
                                )
                            )
                        },
                        # 결정 근거는 기존 JSON 컬럼에 남긴다(마이그레이션 없음).
                        # brief_status는 그대로 비워 둬 생성 시점 브리프 승인 경로를 막지 않는다.
                        content_brief=(
                            {PLANNING_REASON_KEY: slot.planning_reason}
                            if slot.planning_reason
                            else None
                        ),
                    )
                )
            db.flush()
    except IntegrityError:
        # A uniqueness race is harmless only when the full month obligation now
        # exists. FK/check/storage failures must reach the per-hospital incident
        # handler instead of being misreported as an already completed calendar.
        actual = db.execute(month_items_query(hospital.id, next_month)).all()
        if len(actual) < planned_total:
            raise
        logger.info(
            "Next month slots already claimed concurrently: %s %s",
            hospital.name,
            next_month.format("YYYY-MM"),
        )
        return False

    logger.info(
        "Next month slots created: %s %s (%s slots, %s gap-driven)",
        hospital.name,
        next_month.format("YYYY-MM"),
        len(planned),
        sum(1 for slot in planned if slot.query_target_id),
    )
    return True
