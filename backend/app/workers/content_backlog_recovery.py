"""Move missed, ungenerated content back into a safe publication window.

The normal generator and publisher intentionally look back only seven days.  That
keeps a long outage from dumping a month of stale posts in one morning, but it also
means an item can become permanently invisible after the window closes.  This
reconciler gives each stranded item a distinct future date; the existing nightly
generator and morning safety gate then handle it normally.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Final

import arrow
from celery import current_task
from sqlalchemy import or_, select

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentSchedule
from app.models.hospital import Hospital, HospitalStatus
from app.services.audit_log import write_audit_log_sync
from app.utils.db_locks import acquire_hospital_advisory_lock_sync
from app.workers.dispatch_auth import require_dispatch
from app.workers.nightly_generation_batch import (
    GENERATION_WRITE_BACK_STATUSES,
    _needs_generation_recovery,
)

BACKLOG_RECOVERY_CAP: Final = 100
RECOVERABLE_STATUSES: Final = GENERATION_WRITE_BACK_STATUSES


def _stranded_content_stmt(today: date):
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .join(ContentSchedule, ContentItem.schedule_id == ContentSchedule.id)
        .where(
            ContentItem.scheduled_date <= today,
            ContentItem.status.in_(RECOVERABLE_STATUSES),
            _needs_generation_recovery(),
            Hospital.status == HospitalStatus.ACTIVE,
            Hospital.site_live.is_(True),
            or_(ContentSchedule.is_active.is_(True), ContentItem.carried_over_from.is_not(None)),
        )
        .order_by(ContentItem.hospital_id, ContentItem.scheduled_date, ContentItem.sequence_no)
        .with_for_update(skip_locked=True, of=ContentItem)
        .limit(BACKLOG_RECOVERY_CAP)
    )


def _next_available_dates(*, today: date, occupied_dates: set[date], count: int) -> list[date]:
    """Return distinct future dates without disturbing already planned slots.

    Recovery dates deliberately use empty calendar days, including an occasional
    off-schedule weekday.  Planned slots already occupy the configured weekdays;
    restricting recovery to those same weekdays would either collide with the next
    monthly calendar or publish several articles in one morning.  The audited Admin
    reschedule endpoint permits this same exceptional placement.
    """

    available: list[date] = []
    candidate = today + timedelta(days=1)
    while len(available) < count:
        if candidate not in occupied_dates:
            available.append(candidate)
            occupied_dates.add(candidate)
        candidate += timedelta(days=1)
    return available


@celery_app.task(name="app.workers.content_backlog_recovery.reconcile")
def reconcile() -> dict[str, int]:
    """Reschedule stranded items one per hospital/day, preserving future slots."""

    require_dispatch(current_task, "reconcile-stranded-content")
    today = arrow.now("Asia/Seoul").date()
    with SyncSessionLocal() as db:
        stranded = list(db.execute(_stranded_content_stmt(today)).scalars().all())
        grouped: dict[object, list[ContentItem]] = defaultdict(list)
        for item in stranded:
            grouped[item.hospital_id].append(item)

        moved = 0
        for hospital_id, items in grouped.items():
            # Two redelivered/split batches for the same hospital must not choose the
            # same future gaps.  The transaction-scoped lock serializes the occupancy
            # read and all date writes while still allowing different hospitals to run.
            acquire_hospital_advisory_lock_sync(db, hospital_id)
            occupied_dates = set(
                db.execute(
                    select(ContentItem.scheduled_date).where(
                        ContentItem.hospital_id == hospital_id,
                        ContentItem.scheduled_date > today,
                    )
                ).scalars()
            )
            recovery_dates = _next_available_dates(
                today=today,
                occupied_dates=occupied_dates,
                count=len(items),
            )
            for item, recovery_date in zip(items, recovery_dates, strict=True):
                previous_date = item.scheduled_date
                item.scheduled_date = recovery_date
                if (
                    previous_date
                    and (previous_date.year, previous_date.month)
                    != (recovery_date.year, recovery_date.month)
                    and item.carried_over_from is None
                ):
                    item.carried_over_from = previous_date
                write_audit_log_sync(
                    db,
                    action="reschedule_stranded_content",
                    hospital_id=hospital_id,
                    actor="system:content-backlog-recovery",
                    target_type="content_item",
                    target_id=item.id,
                    detail={
                        "previous_scheduled_date": str(previous_date),
                        "scheduled_date": str(recovery_date),
                        "status": item.status.value,
                        "reason": "missed_generation_window",
                    },
                )
                moved += 1
        db.commit()

    return {"rescheduled": moved, "hospitals": len(grouped)}
