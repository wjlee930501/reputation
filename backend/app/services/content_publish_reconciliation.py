"""Fail-closed inventory and bounded drain for retired publication notifications."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import String, cast, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit import AdminAuditLog
from app.models.operations import NotificationOutbox, NotificationOutboxState
from app.services.content_publish_delivery import (
    LEGACY_PUBLISH_NOTIFICATION_TYPE,
    apply_legacy_publish_delivery,
)

_BATCH_SIZE = 200


@dataclass(frozen=True, slots=True)
class LegacyPublishInventory:
    """Count-only retirement proof; payloads and customer data never leave the DB."""

    total: int
    open_transport: int
    unapplied_sent: int

    @property
    def retirable(self) -> bool:
        return self.open_transport == 0 and self.unapplied_sent == 0


@dataclass(frozen=True, slots=True)
class LegacyPublishRetirementBlocked(RuntimeError):
    inventory: LegacyPublishInventory

    def __str__(self) -> str:
        return (
            "legacy publication notification retirement blocked: "
            f"open_transport={self.inventory.open_transport}, "
            f"unapplied_sent={self.inventory.unapplied_sent}"
        )


def _application_marker():
    return select(AdminAuditLog.id).where(
        AdminAuditLog.action == "post_publish_notification_delivery_applied",
        AdminAuditLog.target_type == "notification_outbox",
        AdminAuditLog.target_id == cast(NotificationOutbox.id, String),
    )


async def inspect_legacy_publish_history(
    sessions: async_sessionmaker[AsyncSession],
) -> LegacyPublishInventory:
    """Read only the two counts that gate retirement and the preserved total."""

    async with sessions() as db:
        total = await db.scalar(
            select(func.count(NotificationOutbox.id)).where(
                NotificationOutbox.notification_type == LEGACY_PUBLISH_NOTIFICATION_TYPE
            )
        )
        open_transport = await db.scalar(
            select(func.count(NotificationOutbox.id)).where(
                NotificationOutbox.notification_type == LEGACY_PUBLISH_NOTIFICATION_TYPE,
                NotificationOutbox.state.in_(
                    (
                        NotificationOutboxState.PENDING.value,
                        NotificationOutboxState.SENDING.value,
                        NotificationOutboxState.RETRYING.value,
                        NotificationOutboxState.HOLD.value,
                    )
                ),
            )
        )
        unapplied_sent = await db.scalar(
            select(func.count(NotificationOutbox.id)).where(
                NotificationOutbox.notification_type == LEGACY_PUBLISH_NOTIFICATION_TYPE,
                NotificationOutbox.state == NotificationOutboxState.SENT.value,
                NotificationOutbox.sent_at.is_not(None),
                ~exists(_application_marker()),
            )
        )
    return LegacyPublishInventory(
        total=int(total or 0),
        open_transport=int(open_transport or 0),
        unapplied_sent=int(unapplied_sent or 0),
    )


async def require_legacy_publish_history_drained(
    sessions: async_sessionmaker[AsyncSession],
) -> LegacyPublishInventory:
    """Fail closed unless production read-only evidence says both blockers are zero."""

    inventory = await inspect_legacy_publish_history(sessions)
    if not inventory.retirable:
        raise LegacyPublishRetirementBlocked(inventory)
    return inventory


async def drain_legacy_publish_history(
    sessions: async_sessionmaker[AsyncSession],
) -> int:
    """Apply historical SENT rows only when no transport row is still live."""

    before = await inspect_legacy_publish_history(sessions)
    if before.open_transport:
        raise LegacyPublishRetirementBlocked(before)

    applied = 0
    attempted: set[uuid.UUID] = set()
    while True:
        async with sessions() as read_db:
            rows = tuple(
                (
                    await read_db.execute(
                        select(NotificationOutbox.id, NotificationOutbox.sent_at)
                        .where(
                            NotificationOutbox.notification_type == LEGACY_PUBLISH_NOTIFICATION_TYPE,
                            NotificationOutbox.state == NotificationOutboxState.SENT.value,
                            NotificationOutbox.sent_at.is_not(None),
                            NotificationOutbox.id.notin_(attempted),
                            ~exists(_application_marker()),
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
                if await apply_legacy_publish_delivery(hook_db, outbox_id, sent_at):
                    applied += 1
