"""Human review policy for content that already passed automatic publication checks."""

from typing import Final

from sqlalchemy import and_, func, or_
from sqlalchemy.sql.elements import ColumnElement

from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus

# The automatic review remains the publication gate. Human review is observability sampling,
# not a second approval queue. Keep one baseline item per monthly sequence, then add only
# machine-observed risk: automatic remediation or a body edit after publication.
POST_PUBLISH_SAMPLE_SEQUENCE: Final = 1


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
            func.coalesce(
                ContentItem.essence_check_summary["automatic_remediation_attempts"].as_integer(),
                0,
            )
            > 0,
            ContentItem.body_updated_at > ContentItem.published_at,
        ),
    )
