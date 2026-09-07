"""Generation evidence provenance and selective Essence revalidation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models.content import ContentStatus
from app.models.essence import HospitalSourceEvidenceNote
from app.services.essence_engine import ESSENCE_STATUS_NEEDS_REVIEW


def _flatten_note_ids(value: object) -> set[str]:
    if isinstance(value, dict):
        result: set[str] = set()
        for nested in value.values():
            result.update(_flatten_note_ids(nested))
        return result
    if isinstance(value, (list, tuple, set)):
        result = set()
        for nested in value:
            result.update(_flatten_note_ids(nested))
        return result
    if value in (None, ""):
        return set()
    try:
        return {str(uuid.UUID(str(value)))}
    except (TypeError, ValueError):
        return set()


def generation_evidence_note_ids(philosophy: Any, approved_brief: dict | None) -> set[str]:
    """Return every evidence note whose source-backed text reached the writer."""

    ids = _flatten_note_ids(getattr(philosophy, "evidence_map", None) or {})
    narrative = (approved_brief or {}).get("treatment_narrative") or {}
    if isinstance(narrative, dict):
        ids.update(_flatten_note_ids(narrative.get("evidence_note_ids")))
    return ids


def build_generation_provenance(
    db,
    *,
    hospital_id: uuid.UUID,
    philosophy: Any,
    approved_brief: dict | None,
) -> dict[str, Any]:
    note_ids = generation_evidence_note_ids(philosophy, approved_brief)
    source_ids: set[str] = set()
    if note_ids:
        parsed_ids = [uuid.UUID(value) for value in sorted(note_ids)]
        source_ids = {
            str(value)
            for value in db.execute(
                select(HospitalSourceEvidenceNote.source_asset_id).where(
                    HospitalSourceEvidenceNote.hospital_id == hospital_id,
                    HospitalSourceEvidenceNote.id.in_(parsed_ids),
                )
            ).scalars()
        }
    return {
        "philosophy_id": str(philosophy.id),
        "source_snapshot_hash": getattr(philosophy, "source_snapshot_hash", None),
        "source_asset_ids": sorted(
            str(value) for value in (getattr(philosophy, "source_asset_ids", None) or [])
        ),
        "evidence_note_ids": sorted(note_ids),
        "evidence_source_asset_ids": sorted(source_ids),
        "brief_schema_version": (approved_brief or {}).get("schema_version"),
        "brief_target_revision": (approved_brief or {}).get("target_revision"),
    }


def removed_generation_source_ids(item: Any, philosophy: Any) -> tuple[str, ...]:
    """Return withdrawn sources actually used by this generated article.

    Legacy rows did not record exact dependencies. They continue through the
    deterministic re-screen path instead of causing a fleet-wide false outage.
    """

    summary = getattr(item, "essence_check_summary", None)
    provenance = summary.get("generation_provenance") if isinstance(summary, dict) else None
    if not isinstance(provenance, dict) or "evidence_source_asset_ids" not in provenance:
        return ()
    dependencies = {
        str(value) for value in (provenance.get("evidence_source_asset_ids") or [])
    }
    current = {
        str(value) for value in (getattr(philosophy, "source_asset_ids", None) or [])
    }
    return tuple(sorted(dependencies - current))


def mark_removed_source_dependency(item: Any, philosophy: Any) -> tuple[str, ...]:
    """Persist a fail-closed, automatically repairable revalidation result."""

    removed = removed_generation_source_ids(item, philosophy)
    if not removed:
        return ()
    previous = getattr(item, "essence_check_summary", None)
    summary = dict(previous) if isinstance(previous, dict) else {}
    source_dependency_stale = {
        "removed_source_asset_ids": list(removed),
        "reviewed_philosophy_id": str(getattr(philosophy, "id", "")),
    }
    published_at = getattr(item, "published_at", None)
    if published_at is not None:
        source_dependency_stale["original_published_at"] = published_at.isoformat()
        source_dependency_stale["original_published_by"] = getattr(
            item, "published_by", None
        )
        scheduled_date = getattr(item, "scheduled_date", None)
        source_dependency_stale["original_scheduled_date"] = (
            scheduled_date.isoformat() if scheduled_date is not None else None
        )
    summary.update(
        {
            "blocking": True,
            "findings": [
                "원고가 의존한 근거 자료가 최신 승인 자료 집합에서 제외되어 자동 재생성이 필요합니다."
            ],
            "source_dependency_stale": source_dependency_stale,
        }
    )
    item.essence_status = ESSENCE_STATUS_NEEDS_REVIEW
    item.essence_check_summary = summary
    if hasattr(item, "last_reviewed_philosophy_id"):
        item.last_reviewed_philosophy_id = getattr(philosophy, "id", None)
    if hasattr(item, "content_revision"):
        item.content_revision = int(getattr(item, "content_revision", 1) or 1) + 1

    # A source withdrawal is an automatic unpublish-and-repair event only for the
    # articles that actually depended on that source. Keep the old body as input
    # provenance until the guarded writer replaces it, while moving the row into
    # the existing REJECTED recovery lane. Due/past rows are scheduled for the
    # next normal nightly run; the original contract date remains in carry-over.
    status = getattr(item, "status", None)
    status_value = getattr(status, "value", status)
    if status_value == ContentStatus.PUBLISHED.value:
        today = datetime.now(ZoneInfo("Asia/Seoul")).date()
        original_date = getattr(item, "scheduled_date", None)
        if original_date is not None and original_date <= today:
            next_date = today.fromordinal(today.toordinal() + 1)
            item.scheduled_date = next_date
            if (
                getattr(item, "carried_over_from", None) is None
                and (original_date.year, original_date.month)
                != (next_date.year, next_date.month)
            ):
                item.carried_over_from = original_date
        item.status = ContentStatus.REJECTED
        for field in (
            "post_publish_notified_at",
            "post_publish_reviewed_at",
            "post_publish_reviewed_by",
            "generation_claimed_at",
            "generation_claim_token",
        ):
            if hasattr(item, field):
                setattr(item, field, None)
    # Deliberately retain content_philosophy_id: it identifies the last baseline
    # the body actually satisfied and makes the nightly recovery selector repair it.
    return removed
