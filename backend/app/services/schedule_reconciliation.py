"""Conservative hospital/month allocation shared by saves and repair.

Legacy duplicate or cancelled allocations count until explicitly reconciled. Never
invent history or silently buy replacement quota. Carryover belongs to its origin.
"""

from sqlalchemy import func, select, update

from app.models.content import ContentItem, ContentStatus


def month_items_query(hospital_id, month):
    contract_date = func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
    return select(
        ContentItem.id,
        ContentItem.sequence_no,
        ContentItem.content_type,
        ContentItem.query_target_id,
        ContentItem.total_count,
        ContentItem.status,
        ContentItem.scheduled_date,
        ContentItem.carried_over_from,
        ContentItem.first_published_at,
        ContentItem.published_at,
        ContentItem.human_edited_at,
        ContentItem.generation_claim_token,
    ).where(
        ContentItem.hospital_id == hospital_id,
        contract_date >= month.floor("month").date(),
        contract_date <= month.ceil("month").date(),
    )


def remaining_month_slots(planned, existing):
    """Count identities, not sequence numbers which repeat across old schedules."""
    if not planned:
        return []
    remaining = max(0, planned[0].total_count - len(existing))
    used = {row.sequence_no for row in existing}
    return [slot for slot in planned if slot.sequence_no not in used][:remaining]


def effective_schedules_query(month_end):
    """Read recorded effective terms only for hospitals with an enabled schedule.

    is_active remains the schedule UI's enabled head. An appended future head
    must not erase the current month's recorded terms while it waits to start.
    Never revive hospitals whose schedules have all been explicitly disabled.
    """
    from app.models.content import ContentSchedule

    enabled = select(ContentSchedule.hospital_id).where(ContentSchedule.is_active)
    ranked = (
        select(
            ContentSchedule.id,
            func.row_number()
            .over(
                partition_by=ContentSchedule.hospital_id,
                order_by=(
                    ContentSchedule.active_from.desc(),
                    ContentSchedule.created_at.desc(),
                    ContentSchedule.id.desc(),
                ),
            )
            .label("position"),
        )
        .where(
            ContentSchedule.active_from <= month_end,
            ContentSchedule.hospital_id.in_(enabled),
        )
        .subquery()
    )
    return (
        select(ContentSchedule)
        .join(ranked, ranked.c.id == ContentSchedule.id)
        .where(ranked.c.position == 1)
    )


async def retime_open_allocations(db, existing, planned, *, today):
    """Move eligible future allocations without deleting text or buying new quota.

    Claimed, previously public, carried-over and human-edited work stays put.
    Guard each UPDATE too: an item can be claimed after the initial selection.
    No content revision or retry fingerprint changes for a date-only correction.
    """
    dates = {slot.sequence_no: slot.scheduled_date for slot in planned}
    changed = 0
    for row in existing:
        current_date = getattr(row, "scheduled_date", None)
        wanted = dates.get(row.sequence_no)
        if current_date is None or wanted is None or current_date < today or wanted == current_date:
            continue
        if any(
            getattr(row, field, None) is not None
            for field in (
                "carried_over_from",
                "first_published_at",
                "published_at",
                "human_edited_at",
                "generation_claim_token",
            )
        ) or getattr(row, "status", None) not in (ContentStatus.DRAFT, ContentStatus.READY):
            continue
        result = await db.execute(
            update(ContentItem)
            .where(
                ContentItem.id == row.id,
                ContentItem.status.in_((ContentStatus.DRAFT, ContentStatus.READY)),
                ContentItem.scheduled_date == current_date,
                ContentItem.first_published_at.is_(None),
                ContentItem.published_at.is_(None),
                ContentItem.human_edited_at.is_(None),
                ContentItem.generation_claim_token.is_(None),
                ContentItem.carried_over_from.is_(None),
            )
            .values(scheduled_date=wanted)
            .execution_options(synchronize_session=False)
        )
        changed += result.rowcount
    return changed
