"""Post-commit domain hooks for notifications with observed delivery success."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import NotificationOutbox
from app.services.content_publish_delivery import apply_publish_notification_sent
from app.services.content_publish_notifications import PUBLISH_NOTIFICATION_TYPE


async def run_notification_success_hook(
    db: AsyncSession, outbox_id: uuid.UUID, sent_at: datetime
) -> None:
    """Route a SENT fact to its typed domain hook without changing outbox truth."""

    notification_type = await db.scalar(
        select(NotificationOutbox.notification_type).where(NotificationOutbox.id == outbox_id)
    )
    if notification_type == PUBLISH_NOTIFICATION_TYPE:
        await apply_publish_notification_sent(db, outbox_id, sent_at)
