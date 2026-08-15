"""Post-commit domain hooks for notifications with observed delivery success."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.operations import Incident, IncidentState, NotificationOutbox
from app.services.content_publish_delivery import apply_publish_notification_sent
from app.services.content_publish_notifications import PUBLISH_NOTIFICATION_TYPE
from app.services.incidents import mark_recovered, mark_retrying

_INCIDENT_CAS_ATTEMPTS = 3
_RECONCILE_BATCH_SIZE = 200


class NotificationIncidentRecoveryPending(RuntimeError):
    def __init__(self, outbox_id: uuid.UUID) -> None:
        self.outbox_id = outbox_id
        super().__init__(f"notification incident recovery pending for outbox {outbox_id}")


async def run_notification_success_hook(
    db: AsyncSession, outbox_id: uuid.UUID, sent_at: datetime
) -> None:
    """Route a SENT fact to its typed domain hook without changing outbox truth."""

    notification_type = await db.scalar(
        select(NotificationOutbox.notification_type).where(NotificationOutbox.id == outbox_id)
    )
    if notification_type == PUBLISH_NOTIFICATION_TYPE:
        await apply_publish_notification_sent(db, outbox_id, sent_at)
        return
    if not await _recover_notification_delivery_incident(db, outbox_id, sent_at):
        await db.rollback()
        raise NotificationIncidentRecoveryPending(outbox_id)
    await db.commit()


async def _recover_notification_delivery_incident(
    db: AsyncSession, outbox_id: uuid.UUID, sent_at: datetime
) -> bool:
    """Recover every unresolved delivery incident whose source is this outbox row."""

    for _attempt in range(_INCIDENT_CAS_ATTEMPTS):
        incidents = tuple(
            (
                await db.scalars(
                    select(Incident)
                    .where(
                        Incident.source_type == "NOTIFICATION_OUTBOX",
                        Incident.source_id == str(outbox_id),
                        Incident.state.in_(
                            (IncidentState.OPEN.value, IncidentState.RETRYING.value)
                        ),
                    )
                    .order_by(Incident.id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        if not incidents:
            return True
        for incident in incidents:
            match IncidentState(incident.state):
                case IncidentState.OPEN:
                    await mark_retrying(
                        db,
                        incident.id,
                        expected_version=incident.version,
                        actor="notification-worker",
                        reason="delivery retry succeeded",
                    )
                case IncidentState.RETRYING:
                    await mark_recovered(
                        db,
                        incident.id,
                        expected_version=incident.version,
                        observed_success=True,
                        actor="notification-worker",
                        reason="Slack delivery observed",
                        now=sent_at,
                    )
                case IncidentState.RECOVERED | IncidentState.ACKNOWLEDGED:
                    pass
    return False


async def reconcile_sent_notification_incidents(
    sessions: async_sessionmaker[AsyncSession],
) -> int:
    """Recover delivery-only incidents left behind after a committed SENT fact."""

    recovered = 0
    attempted: set[uuid.UUID] = set()
    while True:
        async with sessions() as read_db:
            rows = tuple(
                (
                    await read_db.execute(
                        select(NotificationOutbox.id, NotificationOutbox.sent_at)
                        .join(
                            Incident,
                            Incident.source_id == cast(NotificationOutbox.id, String),
                        )
                        .where(
                            NotificationOutbox.state == "SENT",
                            NotificationOutbox.sent_at.is_not(None),
                            NotificationOutbox.id.notin_(attempted),
                            Incident.source_type == "NOTIFICATION_OUTBOX",
                            Incident.state.in_(
                                (IncidentState.OPEN.value, IncidentState.RETRYING.value)
                            ),
                        )
                        .distinct()
                        .order_by(NotificationOutbox.sent_at, NotificationOutbox.id)
                        .limit(_RECONCILE_BATCH_SIZE)
                    )
                ).all()
            )
        if not rows:
            return recovered
        for outbox_id, sent_at in rows:
            attempted.add(outbox_id)
            if sent_at is None:
                continue
            async with sessions() as hook_db:
                if await _recover_notification_delivery_incident(hook_db, outbox_id, sent_at):
                    await hook_db.commit()
                    recovered += 1
                else:
                    await hook_db.rollback()
