from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus

NIGHTLY_GENERATION_CAP = 50
NIGHTLY_GENERATION_CLAIM_TTL_HOURS = 2

GENERATION_CATCHUP_DAYS = 7

# 야간 생성 대상 병원 상태 — PAUSED/ONBOARDING 병원은 생성 비용을 발생시키지 않도록 제외.
NIGHTLY_GENERATION_HOSPITAL_STATUSES = (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN)


def _nightly_generation_claim_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)


def _nightly_generation_claim_filter(claim_cutoff: datetime):
    return or_(
        ContentItem.generation_claimed_at.is_(None),
        ContentItem.generation_claimed_at < claim_cutoff,
    )


def _nightly_generation_stmt(window_start, window_end, claim_cutoff: datetime | None = None):
    claim_cutoff = claim_cutoff or _nightly_generation_claim_cutoff()
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.scheduled_date >= window_start,
            ContentItem.scheduled_date <= window_end,
            ContentItem.status.in_([ContentStatus.DRAFT, ContentStatus.REJECTED]),
            ContentItem.body.is_(None),
            Hospital.status.in_(NIGHTLY_GENERATION_HOSPITAL_STATUSES),
            _nightly_generation_claim_filter(claim_cutoff),
        )
        .order_by(
            ContentItem.carried_over_from.is_not(None).desc(),
            ContentItem.scheduled_date,
            ContentItem.sequence_no,
        )
        .options(joinedload(ContentItem.hospital))
        .with_for_update(skip_locked=True, of=ContentItem)
        .limit(NIGHTLY_GENERATION_CAP + 1)
    )


def _load_nightly_generation_batch(db, window_start, window_end) -> tuple[list, int]:
    now = datetime.now(timezone.utc)
    claim_cutoff = now - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)
    result = db.execute(_nightly_generation_stmt(window_start, window_end, claim_cutoff))
    items = list(result.scalars().all())
    truncated_count = 0
    if len(items) > NIGHTLY_GENERATION_CAP:
        overflow = db.execute(
            select(func.count())
            .select_from(ContentItem)
            .join(Hospital, ContentItem.hospital_id == Hospital.id)
            .where(
                ContentItem.scheduled_date >= window_start,
                ContentItem.scheduled_date <= window_end,
                ContentItem.status.in_([ContentStatus.DRAFT, ContentStatus.REJECTED]),
                ContentItem.body.is_(None),
                Hospital.status.in_(NIGHTLY_GENERATION_HOSPITAL_STATUSES),
                _nightly_generation_claim_filter(claim_cutoff),
            )
        ).scalar_one()
        truncated_count = max(int(overflow) - NIGHTLY_GENERATION_CAP, 1)
    claimed_items = items[:NIGHTLY_GENERATION_CAP]
    for item in claimed_items:
        item.generation_claimed_at = now
    if claimed_items:
        db.commit()
    return claimed_items, truncated_count
