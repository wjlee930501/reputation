"""Read monthly obligations, including calendars that were never allocated.

Allocation, first delivery and current public visibility are separate facts.
Carryovers retain their original month. Cancellation is exposed, never revived.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import arrow
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, monthly_quota_for_plan
from app.models.hospital import Hospital
from app.services.post_publish_review_policy import publicly_operational_hospital_predicate
from app.services.schedule_reconciliation import effective_schedules_query


@dataclass(frozen=True, slots=True)
class FleetContractCoverage:
    expected: int = 0
    allocated: int = 0
    first_published: int = 0
    missing_allocations: int = 0
    cancelled_deficit: int = 0
    overdue_unpublished: int = 0
    carried_out_unpublished: int = 0
    unknown_schedule_hospitals: int = 0

    @property
    def delivery_at_risk(self) -> bool:
        return any(
            (
                self.missing_allocations,
                self.cancelled_deficit,
                self.overdue_unpublished,
                self.carried_out_unpublished,
            )
        )


def collect_contract_delivery_coverage(db: Session, *, now: datetime) -> FleetContractCoverage:
    """Four bounded read-only queries for currently operational hospitals.

    A missing/disabled schedule is unknown, not healthy. Recorded effective terms
    own the denominator, not a future head. No provider calls or commits occur.
    """
    if now.tzinfo is None:
        raise ValueError("A timezone-aware observation time is required")
    local = arrow.get(now).to("Asia/Seoul")
    start, end = local.floor("month").date(), local.ceil("month").date()
    hospitals = list(
        db.scalars(select(Hospital.id).where(publicly_operational_hospital_predicate()))
    )
    if not hospitals:
        return FleetContractCoverage()
    hospital_ids = set(hospitals)
    future_hospitals = set(
        db.scalars(
            select(ContentSchedule.hospital_id).where(
                ContentSchedule.hospital_id.in_(hospitals),
                ContentSchedule.is_active.is_(True),
                ContentSchedule.active_from > end,
            )
        )
    )
    schedules = {
        schedule.hospital_id: schedule
        for schedule in db.scalars(effective_schedules_query(end))
        if schedule.hospital_id in hospital_ids
    }
    contract_date = func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
    first = func.coalesce(ContentItem.first_published_at, ContentItem.published_at)
    delivered = and_(first.is_not(None), first <= now)
    undelivered = or_(first.is_(None), first > now)
    open_work = and_(undelivered, ContentItem.status != "CANCELLED")
    # Before 08:00 today's rows have not missed their first release opportunity.
    due_day = local.date() if local.hour >= 8 else local.shift(days=-1).date()
    rows = db.execute(
        select(
            ContentItem.hospital_id,
            func.count(ContentItem.id).label("allocated"),
            func.sum(case((delivered, 1), else_=0)).label("delivered"),
            func.sum(
                case((and_(undelivered, ContentItem.status == "CANCELLED"), 1), else_=0)
            ).label("cancelled"),
            func.sum(
                case((and_(open_work, ContentItem.scheduled_date <= due_day), 1), else_=0)
            ).label("overdue"),
            func.sum(case((and_(open_work, ContentItem.scheduled_date > end), 1), else_=0)).label(
                "carried_out"
            ),
        )
        .where(
            ContentItem.hospital_id.in_(hospitals),
            contract_date >= start,
            contract_date <= end,
        )
        .group_by(ContentItem.hospital_id)
    ).all()
    observed = {row.hospital_id: row for row in rows}
    totals = {field: 0 for field in FleetContractCoverage.__dataclass_fields__}
    for hospital_id in hospitals:
        schedule = schedules.get(hospital_id)
        quota = monthly_quota_for_plan(schedule.plan) if schedule is not None else None
        row = observed.get(hospital_id)
        allocated = int(row.allocated or 0) if row is not None else 0
        delivered_count = int(row.delivered or 0) if row is not None else 0
        cancelled = int(row.cancelled or 0) if row is not None else 0
        totals["allocated"] += allocated
        totals["first_published"] += delivered_count
        totals["overdue_unpublished"] += int(row.overdue or 0) if row is not None else 0
        totals["carried_out_unpublished"] += int(row.carried_out or 0) if row is not None else 0
        if quota is None:
            if schedule is None and hospital_id in future_hospitals:
                continue  # The first recorded contract starts in a future month.
            totals["unknown_schedule_hospitals"] += 1
            continue
        totals["expected"] += quota
        missing = max(0, quota - allocated)
        totals["missing_allocations"] += missing
        # Cancelled extras do not imply a deficit if other identities already
        # cover the purchase. Each missing/cancelled gap is counted only once.
        totals["cancelled_deficit"] += max(0, quota - (allocated - cancelled)) - missing
    return FleetContractCoverage(**totals)
