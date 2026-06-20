import logging
from datetime import date

import arrow
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.content import ContentItem, ContentSchedule, ContentStatus
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

    existing = db.execute(
        select(ContentItem.id).where(
            ContentItem.hospital_id == hospital.id,
            ContentItem.scheduled_date >= next_month_start,
            ContentItem.scheduled_date <= next_month_end,
        ).limit(1)
    )
    if existing.scalar():
        return False

    slots = generate_monthly_slots(schedule.plan, schedule.publish_days, next_month)
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
