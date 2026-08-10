"""Reapply SENT publication hooks that failed after Slack truth was committed."""

from __future__ import annotations

import uuid

from sqlalchemy import String, cast, exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit import AdminAuditLog
from app.models.operations import NotificationOutbox, NotificationOutboxState
from app.services.content_publish_delivery import apply_publish_notification_sent
from app.services.content_publish_notifications import PUBLISH_NOTIFICATION_TYPE

_BATCH_SIZE = 200


async def reconcile_sent_publish_notifications(
    sessions: async_sessionmaker[AsyncSession],
) -> int:
    """Drain every SENT row without a committed domain-application marker."""

    applied = 0
    attempted: set[uuid.UUID] = set()
    while True:
        marker = select(AdminAuditLog.id).where(
            AdminAuditLog.action == "post_publish_notification_delivery_applied",
            AdminAuditLog.target_type == "notification_outbox",
            AdminAuditLog.target_id == cast(NotificationOutbox.id, String),
        )
        async with sessions() as read_db:
            rows = tuple(
                (
                    await read_db.execute(
                        select(NotificationOutbox.id, NotificationOutbox.sent_at)
                        .where(
                            NotificationOutbox.notification_type == PUBLISH_NOTIFICATION_TYPE,
                            NotificationOutbox.state == NotificationOutboxState.SENT.value,
                            NotificationOutbox.sent_at.is_not(None),
                            NotificationOutbox.id.notin_(attempted),
                            ~exists(marker),
                        )
                        .order_by(NotificationOutbox.sent_at, NotificationOutbox.id)
                        .limit(_BATCH_SIZE)
                    )
                ).all()
            )
        if not rows:
            return applied
        for outbox_id, sent_at in rows:
            attempted.add(outbox_id)
            if sent_at is None:
                continue
            async with sessions() as hook_db:
                if await apply_publish_notification_sent(hook_db, outbox_id, sent_at):
                    applied += 1
