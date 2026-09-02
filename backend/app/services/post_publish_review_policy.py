"""Human review policy for content that already passed automatic publication checks."""

from datetime import timedelta
from typing import Final

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus

AUTO_PUBLISHABLE_STATUSES: Final = (ContentStatus.DRAFT, ContentStatus.READY)
AUTO_PUBLISH_CATCHUP_DAYS: Final = 7

# The automatic review remains the publication gate. Human review is observability sampling,
# not a second approval queue — it never blocks monthly report delivery. Keep the sample small:
# one baseline item per monthly sequence, plus any item whose body was edited after publication
# (an AE actually changed something an automated check would not have caught). An item the
# automatic safety gate already remediated is evidence the gate worked, not a reason to sample
# it again, so remediation counts are excluded here.
POST_PUBLISH_SAMPLE_SEQUENCE: Final = 1


def auto_publish_catchup_start(today):
    """Return the oldest scheduled date the autonomous publisher may still catch up."""
    return today - timedelta(days=AUTO_PUBLISH_CATCHUP_DAYS)


def auto_publish_due_predicate(today) -> ColumnElement[bool]:
    """SQL predicate shared by worker and Operations Center for due publish slots."""
    return and_(
        ContentItem.scheduled_date <= today,
        ContentItem.scheduled_date >= auto_publish_catchup_start(today),
        ContentItem.status.in_(AUTO_PUBLISHABLE_STATUSES),
    )


def publicly_operational_hospital_predicate() -> ColumnElement[bool]:
    """Hospital boundary shared by publishing, review queues, and escalation."""
    return and_(
        Hospital.status == HospitalStatus.ACTIVE,
        Hospital.site_live.is_(True),
    )


def human_post_publish_review_predicate() -> ColumnElement[bool]:
    """Return the SQL predicate for the small, non-blocking human quality sample."""
    return and_(
        ContentItem.status == ContentStatus.PUBLISHED,
        ContentItem.post_publish_reviewed_at.is_(None),
        ContentItem.published_at.is_not(None),
        or_(
            ContentItem.sequence_no == POST_PUBLISH_SAMPLE_SEQUENCE,
            ContentItem.body_updated_at > ContentItem.published_at,
        ),
    )


def is_human_post_publish_review_sample(item: ContentItem) -> bool:
    """Return whether a published item belongs to the same human review sample.

    The SQL predicate above is for the live Operations queue, so it also excludes
    already-reviewed rows. Monthly reporting needs the same sample boundary but must count
    both reviewed and pending samples at the reporting cutoff.
    """
    if item.status != ContentStatus.PUBLISHED or item.published_at is None:
        return False
    return item.sequence_no == POST_PUBLISH_SAMPLE_SEQUENCE or (
        item.body_updated_at is not None and item.body_updated_at > item.published_at
    )
