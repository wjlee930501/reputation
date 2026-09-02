"""Query the authoritative publication notification state for Admin projections."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentItem, ContentStatus
from app.models.operations import NotificationOutbox
from app.services.content_publish_notifications import (
    PUBLISH_NOTIFICATION_TYPE,
    PublishNotificationState,
    parse_publish_notification_identity,
    project_publish_notification,
)

_STATE_ADAPTER = TypeAdapter(PublishNotificationState)


async def attach_publish_notification_state(
    db: AsyncSession, items: Sequence[ContentItem]
) -> None:
    """Attach the latest matching outbox projection to ORM rows for Admin serialization."""

    published = tuple(
        item
        for item in items
        if item.status == ContentStatus.PUBLISHED and item.published_at is not None
    )
    if not published:
        return
    hospital_ids = {item.hospital_id for item in published}
    rows = tuple(
        (
            await db.execute(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.hospital_id.in_(hospital_ids),
                    NotificationOutbox.notification_type == PUBLISH_NOTIFICATION_TYPE,
                )
                .order_by(NotificationOutbox.created_at.desc())
            )
        ).scalars()
    )
    matching: dict[tuple[uuid.UUID, datetime], NotificationOutbox] = {}
    for row in rows:
        identity = parse_publish_notification_identity(row.dedupe_key)
        if identity is None:
            continue
        matching.setdefault((identity.content_id, identity.published_at), row)
    for item in published:
        normalized = item.published_at.astimezone(UTC)
        row = matching.get((item.id, normalized))
        state = _parse_state(row.state) if row is not None else None
        item._publish_notification_projection = project_publish_notification(
            state,
            notification_id=row.id if row is not None else None,
            safe_error_code=row.safe_error_code if row is not None else None,
        )


def _parse_state(value: str) -> PublishNotificationState:
    return _STATE_ADAPTER.validate_python(value)
