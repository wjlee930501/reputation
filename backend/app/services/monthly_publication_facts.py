"""Report-time publication facts, kept separate from the currently visible edition.

First publication fulfills a contract even after withdrawal or republication.
The report's observation time and exclusive period end are preserved exactly.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus


def first_publication_at(item: ContentItem) -> datetime | None:
    return getattr(item, "first_published_at", None) or item.published_at


def observed_contract_publications(
    items: Iterable[ContentItem], observed_at: datetime
) -> list[ContentItem]:
    """Only publications that had actually happened at report-build time fulfill a contract."""
    return [
        item
        for item in items
        if first_publication_at(item) is not None and first_publication_at(item) <= observed_at
    ]


def contract_publication_timing_counts(
    items: Iterable[ContentItem],
    period_start: datetime,
    period_end: datetime,
) -> tuple[int, int]:
    """Return contract publications before the period and at/after its exclusive end."""
    early = 0
    late = 0
    for item in items:
        first_published_at = first_publication_at(item)
        if first_published_at is None:
            continue
        if first_published_at < period_start:
            early += 1
        elif first_published_at >= period_end:
            late += 1
    return early, late


def load_monthly_publication_facts(
    db: Session,
    hospital_id: uuid.UUID,
    period_start: datetime,
    period_end: datetime,
    observed_at: datetime,
) -> tuple[list[ContentItem], list[ContentItem], list[ContentItem]]:
    """Load immutable publication facts separately from currently visible rows."""
    first_publication_at = func.coalesce(
        ContentItem.first_published_at,
        ContentItem.published_at,
    )
    actual_publications = list(
        db.execute(
            select(ContentItem).where(
                ContentItem.hospital_id == hospital_id,
                first_publication_at.is_not(None),
                first_publication_at >= period_start,
                first_publication_at < period_end,
                first_publication_at <= observed_at,
            )
        ).scalars()
    )
    visible_publications = [
        item for item in actual_publications if item.status == ContentStatus.PUBLISHED
    ]
    contract_publications = observed_contract_publications(
        db.execute(
            select(ContentItem).where(
                ContentItem.hospital_id == hospital_id,
                first_publication_at.is_not(None),
                first_publication_at <= observed_at,
                func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
                >= period_start.date(),
                func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
                < period_end.date(),
            )
        ).scalars(),
        observed_at,
    )
    return actual_publications, visible_publications, contract_publications
