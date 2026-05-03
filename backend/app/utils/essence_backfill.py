"""Backfill content_philosophy_id / essence_status for legacy ContentItem rows.

Existing ContentItem rows that were created before the Content Essence MVP
have NULL content_philosophy_id and essence_status. This utility fills those
fields by re-screening each item against the hospital's currently-approved
philosophy. If no approved philosophy exists, items are marked
MISSING_APPROVED_PHILOSOPHY without a philosophy link.

Designed to be safe to re-run: only touches rows where essence_status is NULL
(or `force=True`) and never silently claims ALIGNED — the screening pipeline
runs check_forbidden + avoid_messages exactly like the live worker path.
"""
from __future__ import annotations

import argparse
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital
from app.services.essence_engine import (
    ESSENCE_STATUS_MISSING_APPROVED,
    screen_content_against_philosophy,
)

logger = logging.getLogger(__name__)


@dataclass
class BackfillResult:
    hospitals_processed: int = 0
    items_total: int = 0
    items_updated: int = 0
    items_aligned: int = 0
    items_needs_review: int = 0
    items_missing_philosophy: int = 0
    items_skipped_no_body: int = 0


def backfill_hospital_items(
    items,
    approved_philosophy: HospitalContentPhilosophy | None,
) -> BackfillResult:
    """Pure per-hospital backfill loop. Mutates each item in place.

    Extracted so the logic is unit-testable without a real DB session.
    """
    result = BackfillResult(hospitals_processed=1, items_total=len(items))
    for item in items:
        if not item.body:
            result.items_skipped_no_body += 1
            continue

        screening = screen_content_against_philosophy(item, approved_philosophy)
        item.essence_status = screening.status
        item.essence_check_summary = screening.summary
        if approved_philosophy and screening.status != ESSENCE_STATUS_MISSING_APPROVED:
            item.content_philosophy_id = approved_philosophy.id
        elif not approved_philosophy:
            item.content_philosophy_id = None

        result.items_updated += 1
        if screening.status == ESSENCE_STATUS_MISSING_APPROVED:
            result.items_missing_philosophy += 1
        elif screening.status == "NEEDS_ESSENCE_REVIEW":
            result.items_needs_review += 1
        else:
            result.items_aligned += 1
    return result


def _merge(into: BackfillResult, other: BackfillResult) -> None:
    into.hospitals_processed += other.hospitals_processed
    into.items_total += other.items_total
    into.items_updated += other.items_updated
    into.items_aligned += other.items_aligned
    into.items_needs_review += other.items_needs_review
    into.items_missing_philosophy += other.items_missing_philosophy
    into.items_skipped_no_body += other.items_skipped_no_body


def backfill_essence(
    db: Session,
    hospital_id: uuid.UUID | str | None = None,
    force: bool = False,
) -> BackfillResult:
    """Backfill essence fields on ContentItem rows.

    - hospital_id=None  → all hospitals
    - force=False       → only rows with essence_status IS NULL
    - force=True        → all rows in scope, including previously screened ones
    """
    aggregate = BackfillResult()

    hospital_stmt = select(Hospital)
    if hospital_id is not None:
        hospital_stmt = hospital_stmt.where(Hospital.id == _coerce_uuid(hospital_id))
    hospitals = db.execute(hospital_stmt).scalars().all()

    for hospital in hospitals:
        approved = db.execute(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.hospital_id == hospital.id,
                HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
            )
        ).scalar_one_or_none()

        item_stmt = select(ContentItem).where(ContentItem.hospital_id == hospital.id)
        if not force:
            item_stmt = item_stmt.where(ContentItem.essence_status.is_(None))
        items = db.execute(item_stmt).scalars().all()

        _merge(aggregate, backfill_hospital_items(items, approved))
        db.flush()

    db.commit()
    return aggregate


def _coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _main() -> None:
    parser = argparse.ArgumentParser(description="Backfill ContentItem essence fields.")
    parser.add_argument("--hospital-id", help="Restrict to a single hospital UUID.")
    parser.add_argument("--force", action="store_true", help="Re-screen items even if already scored.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with SyncSessionLocal() as db:
        result = backfill_essence(db, hospital_id=args.hospital_id, force=args.force)

    logger.info(
        "essence backfill done: hospitals=%d items=%d updated=%d aligned=%d "
        "needs_review=%d missing=%d skipped_no_body=%d",
        result.hospitals_processed,
        result.items_total,
        result.items_updated,
        result.items_aligned,
        result.items_needs_review,
        result.items_missing_philosophy,
        result.items_skipped_no_body,
    )


if __name__ == "__main__":
    _main()
