"""Human review policy for content that already passed automatic publication checks."""

from typing import Final

from sqlalchemy import and_
from sqlalchemy.sql.elements import ColumnElement

from app.models.content import ContentItem, ContentStatus

# The automatic review remains the publication gate. Human review is observability sampling,
# not a second approval queue: one item per monthly sequence is enough to detect drift without
# asking operators to inspect every successful publication.
POST_PUBLISH_SAMPLE_SEQUENCE: Final = 1


def human_post_publish_review_predicate() -> ColumnElement[bool]:
    """Return the SQL predicate for the small, non-blocking human quality sample."""
    return and_(
        ContentItem.status == ContentStatus.PUBLISHED,
        ContentItem.post_publish_reviewed_at.is_(None),
        ContentItem.published_at.is_not(None),
        ContentItem.sequence_no == POST_PUBLISH_SAMPLE_SEQUENCE,
    )
