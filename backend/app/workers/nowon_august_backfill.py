"""One-off August 2026 slot close for 노원탑365의원.

This module deliberately owns its database session so the normal monthly-slot
transaction and its tests remain isolated from the hospital-specific queries.
"""

import logging
import uuid
from collections import Counter
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import SyncSessionLocal
from app.models.content import (
    PLAN_DISTRIBUTION,
    ContentItem,
    ContentSchedule,
    ContentStatus,
    ContentType,
)
from app.models.hospital import Hospital
from app.services.content_calendar import _interleave_types
from app.workers.dispatch_auth import build_dispatch_headers

logger = logging.getLogger(__name__)

NOWON_HOSPITAL_NAME = "노원탑365의원"
NOWON_HOSPITAL_ID = uuid.UUID("8fd5c4a9-dac9-4dd7-a02b-b25f5921882f")
AUGUST_START = date(2026, 8, 1)
AUGUST_END = date(2026, 8, 31)
BACKFILL_TARGET = 12
PLANNED_DATES = [
    date(2026, 8, 26),
    date(2026, 8, 26),
    date(2026, 8, 27),
    date(2026, 8, 27),
    date(2026, 8, 28),
    date(2026, 8, 28),
    date(2026, 8, 29),
    date(2026, 8, 30),
    date(2026, 8, 31),
]


def _remaining_plan_12_types(
    existing_items: list[ContentItem],
    missing: int,
    hospital_id: object,
) -> list[ContentType]:
    """Return a deterministic PLAN_12 remainder without introducing NOTICE."""
    existing_counts: Counter[ContentType] = Counter()
    for item in existing_items:
        try:
            existing_counts[ContentType(item.content_type)] += 1
        except (TypeError, ValueError):
            # Legacy/odd types do not consume a PLAN_12 allocation.
            continue

    planned_types = _interleave_types(
        PLAN_DISTRIBUTION["PLAN_12"],
        seed=f"{hospital_id}:2026-08:nowon-12-close",
    )
    remaining: list[ContentType] = []
    for content_type in planned_types:
        if existing_counts[content_type] > 0:
            existing_counts[content_type] -= 1
        else:
            remaining.append(content_type)

    if len(remaining) < missing:
        raise ValueError("PLAN_12 유형 배분으로 노원탑 8월 누락 슬롯을 채울 수 없습니다.")
    return remaining[:missing]


def _enqueue_created_drafts(item_ids: list[str]) -> None:
    """Generate every committed draft, then run the existing morning publisher."""
    from app.workers.tasks import morning_content_auto_publish, regenerate_content_item

    for item_id in item_ids:
        try:
            regenerate_content_item.apply_async(
                args=[item_id],
                queue="content",
                headers=build_dispatch_headers("regenerate-content", item_id),
            )
        except Exception:
            logger.exception("Nowon August draft enqueue failed: item=%s", item_id)

    try:
        morning_content_auto_publish.apply_async(
            queue="content",
            headers=build_dispatch_headers("morning-content-auto-publish"),
        )
    except Exception:
        logger.exception("Nowon August morning auto-publish enqueue failed")


def backfill_nowon_august_2026_slots() -> int:
    """Create the missing hospital-scoped August slots, commit, then dispatch them."""
    created_item_ids: list[str] = []

    with SyncSessionLocal() as db:
        hospital = db.execute(
            select(Hospital).where(Hospital.id == NOWON_HOSPITAL_ID).limit(1)
        ).scalars().first()
        if hospital is None:
            hospital = db.execute(
                select(Hospital).where(Hospital.name == NOWON_HOSPITAL_NAME).limit(1)
            ).scalars().first()
        if hospital is None:
            return 0

        schedule = (
            db.execute(
                select(ContentSchedule)
                .where(
                    ContentSchedule.hospital_id == hospital.id,
                    ContentSchedule.is_active.is_(True),
                )
                .order_by(ContentSchedule.created_at.desc(), ContentSchedule.id.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if schedule is None:
            return 0

        existing_items = list(
            db.execute(
                select(ContentItem).where(
                    ContentItem.hospital_id == hospital.id,
                    ContentItem.scheduled_date >= AUGUST_START,
                    ContentItem.scheduled_date <= AUGUST_END,
                    ContentItem.status != ContentStatus.CANCELLED,
                )
            )
            .scalars()
            .all()
        )
        missing = BACKFILL_TARGET - len(existing_items)
        if missing <= 0:
            return 0

        occupied_dates = Counter(item.scheduled_date for item in existing_items)
        slot_dates: list[date] = []
        for planned_date in PLANNED_DATES:
            if occupied_dates[planned_date] > 0:
                occupied_dates[planned_date] -= 1
            else:
                slot_dates.append(planned_date)
        slot_dates = slot_dates[:missing]
        if not slot_dates:
            return 0

        content_types = _remaining_plan_12_types(existing_items, missing, hospital.id)
        used_sequences = {item.sequence_no for item in existing_items}
        sequence_numbers = [
            sequence
            for sequence in range(1, BACKFILL_TARGET + 1)
            if sequence not in used_sequences
        ][:missing]

        new_items = [
            ContentItem(
                hospital_id=hospital.id,
                schedule_id=schedule.id,
                content_type=content_type,
                sequence_no=sequence_no,
                total_count=BACKFILL_TARGET,
                scheduled_date=scheduled_date,
                status=ContentStatus.DRAFT,
            )
            for scheduled_date, content_type, sequence_no in zip(
                slot_dates,
                content_types,
                sequence_numbers,
            )
        ]

        try:
            with db.begin_nested():
                db.add_all(new_items)
                db.flush()
        except IntegrityError:
            logger.info("Nowon August slots were already claimed concurrently")
            return 0

        db.commit()
        created_item_ids = [str(item.id) for item in new_items]

    _enqueue_created_drafts(created_item_ids)
    logger.info("Nowon August backfill created and dispatched %d slots", len(created_item_ids))
    return len(created_item_ids)
