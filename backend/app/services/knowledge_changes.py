"""Explicit authority changes; retain the stable base for unaffected public reads.

Ordinary new sources do not resynthesise a base. Withdrawal/correction of a source
actually used by the base blocks NEW generation until one bounded reapproval.
Only dependent articles are withdrawn. Missing legacy lineage is labelled unknown,
never fabricated as exact source attribution. Call inside the source transaction.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.services.essence_engine import ESSENCE_STATUS_NEEDS_REVIEW

AUTHORITY_CHANGE_FIELD = "authority_change_required"


def authority_refresh_required(base) -> bool:
    return any(
        isinstance(gap, dict) and gap.get("field") == AUTHORITY_CHANGE_FIELD
        for gap in (getattr(base, "unsupported_gaps", None) or [])
    )


def invalidate_article_authority(item, *, source_id, base, reason, now):
    summary = dict(item.essence_check_summary or {})
    provenance = summary.get("generation_provenance")
    exact = isinstance(provenance, dict) and "evidence_source_asset_ids" in provenance
    dependencies = set(
        str(value)
        for value in (
            provenance.get("evidence_source_asset_ids", ())
            if exact
            else base.source_asset_ids or ()
        )
    )
    if str(source_id) not in dependencies:
        return False
    prior = summary.get("authority_change") or {}
    source_ids = set(prior.get("source_ids", ()))
    if str(source_id) in source_ids and prior.get("reason") == reason:
        return False
    source_ids.add(str(source_id))
    summary["authority_change"] = {
        "source_ids": sorted(source_ids),
        "reason": reason,
        "dependency_certainty": "EXACT" if exact else "UNKNOWN_LEGACY",
        "requested_at": now.isoformat(),
    }
    summary["blocking"] = True
    summary["findings"] = ["원고의 근거 자료가 변경되어 새 기준에 따른 재검토가 필요합니다."]
    item.essence_check_summary = summary
    item.essence_status = ESSENCE_STATUS_NEEDS_REVIEW
    item.content_revision = int(item.content_revision or 1) + 1
    item.generation_claim_token = None
    item.generation_claimed_at = None
    if item.published_at is not None and item.first_published_at is None:
        item.first_published_at, item.first_published_by = item.published_at, item.published_by
    if item.status == ContentStatus.PUBLISHED:
        item.status = ContentStatus.REJECTED
    local_day = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    old_date = item.scheduled_date
    if old_date and old_date <= local_day:
        item.scheduled_date = local_day + timedelta(days=1)
        if item.carried_over_from is None and (old_date.year, old_date.month) != (
            item.scheduled_date.year,
            item.scheduled_date.month,
        ):
            item.carried_over_from = old_date
    return True


async def invalidate_source_authority(db, hospital_id, source_id, *, reason):
    base = (
        await db.execute(
            select(HospitalContentPhilosophy)
            .where(
                HospitalContentPhilosophy.hospital_id == hospital_id,
                HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
            )
            .order_by(
                HospitalContentPhilosophy.is_base.desc(),
                HospitalContentPhilosophy.approved_at.desc().nullslast(),
                HospitalContentPhilosophy.version.desc(),
            )
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if base is None or str(source_id) not in {
        str(value) for value in (base.source_asset_ids or [])
    }:
        return []
    now = datetime.now(UTC)
    gaps = list(base.unsupported_gaps or [])
    marker = next(
        (
            gap
            for gap in gaps
            if isinstance(gap, dict) and gap.get("field") == AUTHORITY_CHANGE_FIELD
        ),
        {},
    )
    ids = set(marker.get("source_ids", ()))
    ids.add(str(source_id))
    base.unsupported_gaps = [
        gap
        for gap in gaps
        if not (isinstance(gap, dict) and gap.get("field") == AUTHORITY_CHANGE_FIELD)
    ] + [
        {
            "field": AUTHORITY_CHANGE_FIELD,
            "source_ids": sorted(ids),
            "reason": reason,
            "requested_at": now.isoformat(),
        }
    ]
    rows = (
        (
            await db.execute(
                select(ContentItem)
                .where(
                    ContentItem.hospital_id == hospital_id,
                    ContentItem.content_philosophy_id == base.id,
                    ContentItem.status != ContentStatus.CANCELLED,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    changed = [
        item.id
        for item in rows
        if invalidate_article_authority(
            item, source_id=source_id, base=base, reason=reason, now=now
        )
    ]

    # A newly claimed empty slot may not yet reference the base it read. Fence
    # every in-flight generation in this hospital before committing the authority
    # change, including those rows; otherwise its old result can arrive after the
    # replacement-base rescreen and escape that rescreen entirely. No provider IO.
    await db.execute(
        update(ContentItem)
        .where(
            ContentItem.hospital_id == hospital_id,
            ContentItem.status.in_(
                (ContentStatus.DRAFT, ContentStatus.READY, ContentStatus.REJECTED)
            ),
            ContentItem.generation_claim_token.is_not(None),
        )
        .values(
            generation_claim_token=None,
            generation_claimed_at=None,
            content_revision=ContentItem.content_revision + 1,
        )
        .execution_options(synchronize_session=False)
    )
    return changed
