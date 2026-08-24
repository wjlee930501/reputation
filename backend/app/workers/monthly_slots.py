import logging
from datetime import date

import arrow
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.content import PLAN_DISTRIBUTION, ContentItem, ContentSchedule, ContentStatus
from app.models.hospital import HospitalStatus
from app.services.content_calendar import generate_monthly_slots

logger = logging.getLogger(__name__)


def create_next_month_slots_for_schedule(
    db,
    schedule: ContentSchedule,
    next_month: arrow.Arrow,
    next_month_start: date,
    next_month_end: date,
) -> bool:
    hospital = schedule.hospital
    if hospital.status not in (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN):
        return False

    # 스케줄 활성화 전 달은 발행 대상이 아니다. active_from이 대상 월보다 완전히 뒤면
    # (예: 7/25 배치가 8월분을 만드는데 active_from=2026-09-01) 슬롯을 만드는 순간
    # 계약보다 한 달 일찍 자동 발행이 시작된다.
    active_from = schedule.active_from
    if active_from and active_from > next_month_end:
        return False
    # 활성화일이 대상 월 중간에 걸리면 그 날짜 이후로만 발행해야 한다.
    start_date = active_from if active_from and active_from > next_month_start else None

    # "이 스케줄의 이번 달 계획 슬롯이 이미 만들어졌는가"로만 판정한다.
    # 월 전체에 아이템이 1건이라도 있으면 건너뛰던 과거 조건은, 지난달에서 이월된
    # (carried_over_from) 1건이나 다른 스케줄의 행 하나가 다음 달 약정 편수 전체 생성을
    # 통째로 막았다. 이월 슬롯은 계획 편수에 포함되지 않으므로 판정에서 제외한다.
    existing_sequences = set(
        db.execute(
            select(ContentItem.sequence_no).where(
                ContentItem.schedule_id == schedule.id,
                ContentItem.carried_over_from.is_(None),
                ContentItem.scheduled_date >= next_month_start,
                ContentItem.scheduled_date <= next_month_end,
            )
        ).scalars().all()
    )
    planned_total = sum(PLAN_DISTRIBUTION.get(schedule.plan, {}).values())
    if planned_total and len(existing_sequences) >= planned_total:
        return False

    slots = generate_monthly_slots(
        schedule.plan,
        schedule.publish_days,
        next_month,
        start_date,
        allow_shortfall=True,
    )
    # 부분 생성(중단된 이전 배치 등) 뒤에는 비어 있는 순번만 채운다.
    slots = [slot for slot in slots if slot[2] not in existing_sequences]
    if not slots:
        return False

    try:
        with db.begin_nested():
            for slot_date, ctype, seq_no, total in slots:
                db.add(
                    ContentItem(
                        hospital_id=hospital.id,
                        schedule_id=schedule.id,
                        content_type=ctype,
                        sequence_no=seq_no,
                        total_count=total,
                        scheduled_date=slot_date,
                        status=ContentStatus.DRAFT,
                    )
                )
            db.flush()
    except IntegrityError:
        logger.info(
            "Next month slots already claimed concurrently: %s %s",
            hospital.name,
            next_month.format("YYYY-MM"),
        )
        return False

    logger.info(
        "Next month slots created: %s %s (%s slots)",
        hospital.name,
        next_month.format("YYYY-MM"),
        len(slots),
    )
    return True
