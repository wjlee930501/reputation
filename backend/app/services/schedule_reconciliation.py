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


# ── 첫 발행 전 시작일 미루기 ────────────────────────────────────────────────
#
# 아직 어떤 일정도 시작하지 않았고 한 편도 공개한 적 없는 병원의 일정을 저장하면, 새 시작일의
# 달보다 앞 달에 남은 슬롯은 계약 시작 전 슬롯이다(2026-11-12 365서귀포삼성정형외과: 11/13
# 시작을 12/1로 옮겼는데 11월 슬롯 8개가 남아 그대로 생성·발행될 뻔했다). 이때 그 앞 달의
# 미발행 슬롯을 종료(CANCELLED)하고, 그 앞 달들을 미룬 달로 기록해 약속을 살아 있는 슬롯 수(보통
# 0편)로 둔다(`content_month_target.month_is_voided`가 읽는다). 사람이 고친 원고는 종료하지 않는다.
# 한 일정이라도 이미 시작했거나(시작일 ≤ 오늘) 공개한 글이 있으면 대상이 아니다 — 그때 늦은
# 시작일은 "그날부터 새 조건"이고 그 사이 달의 약속은 옛 조건 그대로다.

DEFERRED_START_KEY = "deferred_start"
VOIDED_MONTHS_KEY = "voided_months"
_DEFERRABLE_STATUSES = (ContentStatus.DRAFT, ContentStatus.READY, ContentStatus.REJECTED)


async def defer_unstarted_contract(db, hospital_id, *, new_start, today):
    """계약 시작 전 병원의 새 시작 달 앞 슬롯을 종료한다. 대상이 아니거나 종료할 것이 없으면 None.

    새 일정 행을 넣기 전에 부른다. 반환값은 감사 detail에 그대로 싣는 dict다
    (`voided_months`는 약속 0편 달 "YYYY-MM").
    """

    import arrow

    from app.models.content import ContentSchedule

    earliest_start = (
        await db.execute(
            select(func.min(ContentSchedule.active_from)).where(
                ContentSchedule.hospital_id == hospital_id
            )
        )
    ).scalar_one_or_none()
    if earliest_start is None or earliest_start <= today:
        return None
    published = func.coalesce(ContentItem.first_published_at, ContentItem.published_at)
    ever_published = (
        await db.execute(
            select(ContentItem.id)
            .where(
                ContentItem.hospital_id == hospital_id,
                (published.is_not(None))
                | ContentItem.status.in_((ContentStatus.PUBLISHED, ContentStatus.WITHHELD)),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if ever_published is not None:
        return None

    new_month = arrow.get(new_start).floor("month").date()
    contract_date = func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
    rows = (
        await db.execute(
            select(
                ContentItem.id,
                ContentItem.scheduled_date,
                ContentItem.carried_over_from,
                ContentItem.human_edited_at,
            )
            .where(
                ContentItem.hospital_id == hospital_id,
                contract_date < new_month,
                ContentItem.status.in_(_DEFERRABLE_STATUSES),
                published.is_(None),
            )
            .order_by(ContentItem.scheduled_date, ContentItem.id)
            .with_for_update()
        )
    ).all()
    if not rows:
        return None
    cancelled: list[str] = []
    kept: list[str] = []
    for row in rows:
        if row.human_edited_at is not None:
            kept.append(str(row.id))
            continue
        result = await db.execute(
            update(ContentItem)
            .where(
                ContentItem.id == row.id,
                ContentItem.status.in_(_DEFERRABLE_STATUSES),
                ContentItem.first_published_at.is_(None),
                ContentItem.published_at.is_(None),
                ContentItem.human_edited_at.is_(None),
            )
            .values(
                status=ContentStatus.CANCELLED,
                generation_claimed_at=None,
                generation_claim_token=None,
                content_revision=func.coalesce(ContentItem.content_revision, 1) + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            cancelled.append(str(row.id))
        else:
            kept.append(str(row.id))

    # 계약 시작 전의 모든 달(가장 이른 일정 시작 달 ~ 새 시작 달 앞)을 미룬 달로 기록한다 — 슬롯이
    # 없던 달도 넣어야 당월 조정이 옛 일정으로 그 달 슬롯을 새로 만들지 않는다. 미룬 달의 약속은
    # 살아 있는 슬롯 수(사람이 고쳐 남긴 원고)까지다(`content_month_target.resolve_month_target`).
    contract_dates = [
        row.carried_over_from or row.scheduled_date
        for row in rows
        if (row.carried_over_from or row.scheduled_date) is not None
    ]
    month = arrow.get(min([earliest_start, *contract_dates])).floor("month")
    voided: list[str] = []
    while month.date() < new_month:
        voided.append(month.format("YYYY-MM"))
        month = month.shift(months=1)
    return {
        "earliest_schedule_start": str(earliest_start),
        "new_start": str(new_start),
        "cancelled_ids": cancelled,
        "kept_human_edited_ids": kept,
        VOIDED_MONTHS_KEY: voided,
    }
