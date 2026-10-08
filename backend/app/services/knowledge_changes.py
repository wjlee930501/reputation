"""Explicit authority changes with stable public reads for unaffected content."""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.services.content_provenance import generation_input_source_ids
from app.services.essence_engine import ESSENCE_STATUS_NEEDS_REVIEW

AUTHORITY_CHANGE_FIELD: Final = "authority_change_required"
SOURCE_VERSION_METADATA_KEY: Final = "_source_version"
FUTURE_ONLY_ADDENDUM_MODE: Final = "FUTURE_ONLY_ADDENDUM"


def _valid_source_version_lineage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("mode") != FUTURE_ONLY_ADDENDUM_MODE:
        return None
    root_source_id = value.get("root_source_id")
    previous_source_id = value.get("previous_source_id")
    version = value.get("version")
    if (
        not isinstance(root_source_id, str)
        or not isinstance(previous_source_id, str)
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version < 2
    ):
        return None
    try:
        root = str(uuid.UUID(root_source_id))
        previous = str(uuid.UUID(previous_source_id))
    except ValueError:
        return None
    return {
        "mode": FUTURE_ONLY_ADDENDUM_MODE,
        "root_source_id": root,
        "previous_source_id": previous,
        "version": version,
    }


def without_source_version_authority(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Remove server-owned source lineage from client-controlled metadata."""

    cleaned = dict(metadata or {})
    cleaned.pop(SOURCE_VERSION_METADATA_KEY, None)
    return cleaned


def preserve_source_version_authority(
    current: Mapping[str, Any] | None,
    requested: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Apply a metadata patch while retaining server-owned source lineage."""

    merged = without_source_version_authority(requested)
    current_lineage = _valid_source_version_lineage(
        (current or {}).get(SOURCE_VERSION_METADATA_KEY)
    )
    if current_lineage is not None:
        merged[SOURCE_VERSION_METADATA_KEY] = current_lineage
    return merged


def future_addendum_metadata(
    source: Any,
    requested: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build server-owned lineage for a new future-only source addendum."""

    parent_metadata = getattr(source, "source_metadata", None)
    parent_lineage = (
        parent_metadata.get(SOURCE_VERSION_METADATA_KEY)
        if isinstance(parent_metadata, dict)
        else None
    )
    parent_version = 1
    root_source_id = str(source.id)
    valid_parent_lineage = _valid_source_version_lineage(parent_lineage)
    if valid_parent_lineage is not None:
        parent_version = valid_parent_lineage["version"]
        root_source_id = valid_parent_lineage["root_source_id"]
    return {
        **without_source_version_authority(requested),
        SOURCE_VERSION_METADATA_KEY: {
            "mode": FUTURE_ONLY_ADDENDUM_MODE,
            "root_source_id": root_source_id,
            "previous_source_id": str(source.id),
            "version": parent_version + 1,
        },
    }


def authority_refresh_required(base) -> bool:
    return any(
        isinstance(gap, dict) and gap.get("field") == AUTHORITY_CHANGE_FIELD
        for gap in (getattr(base, "unsupported_gaps", None) or [])
    )


def invalidate_article_authority(item, *, source_id, base, reason, now):
    summary = dict(item.essence_check_summary or {})
    generation_inputs = generation_input_source_ids(item)
    exact = generation_inputs is not None
    dependencies = set(
        str(value)
        for value in (
            generation_inputs
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
        **prior,
        "source_ids": sorted(source_ids),
        "reason": reason,
        "dependency_certainty": "EXACT_INPUT" if exact else "UNKNOWN_LEGACY",
        "dependency_scope": "GENERATION_INPUT" if exact else "UNKNOWN",
        "semantic_claim_dependency": False,
        "requested_at": now.isoformat(),
    }
    summary["blocking"] = True
    summary["findings"] = ["원고의 근거 자료가 변경되어 새 기준에 따른 재검토가 필요합니다."]
    item.essence_check_summary = summary
    item.essence_status = ESSENCE_STATUS_NEEDS_REVIEW
    item.content_revision = int(item.content_revision or 1) + 1
    item.generation_claim_token = None
    item.generation_claimed_at = None
    if hasattr(item, "active_revision_id"):
        item.active_revision_id = None
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
            **marker,
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
