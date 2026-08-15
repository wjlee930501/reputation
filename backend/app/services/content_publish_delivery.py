"""Apply observed publication-notification delivery without republishing content."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentStatus
from app.models.operations import Incident, IncidentState, NotificationOutbox
from app.services.audit_log import write_audit_log
from app.services.content_publish_notifications import (
    PUBLISH_NOTIFICATION_TYPE,
    parse_publish_notification_identity,
)
from app.services.incidents import mark_recovered, mark_retrying

_INCIDENT_CAS_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class PublishIncidentRecoveryPending(RuntimeError):
    outbox_id: uuid.UUID

    def __str__(self) -> str:
        return f"publish incident recovery pending for outbox {self.outbox_id}"


async def apply_publish_notification_sent(
    db: AsyncSession, outbox_id: uuid.UUID, sent_at: datetime
) -> bool:
    """Stamp the matching publication and recover its incident after observed delivery."""

    outbox = await db.scalar(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == outbox_id)
        .with_for_update()
    )
    if outbox is None or outbox.notification_type != PUBLISH_NOTIFICATION_TYPE:
        return False
    marker_exists = await db.scalar(
        select(AdminAuditLog.id).where(
            AdminAuditLog.action == "post_publish_notification_delivery_applied",
            AdminAuditLog.target_type == "notification_outbox",
            AdminAuditLog.target_id == str(outbox_id),
        )
    )
    if marker_exists is not None:
        return False
    identity = parse_publish_notification_identity(outbox.dedupe_key)
    if identity is None:
        return False
    hospital_id = (
        await db.execute(
            update(ContentItem)
            .where(
                ContentItem.id == identity.content_id,
                ContentItem.status == ContentStatus.PUBLISHED,
                ContentItem.published_at == identity.published_at,
                ContentItem.post_publish_notified_at.is_(None),
            )
            .values(post_publish_notified_at=sent_at)
            .returning(ContentItem.hospital_id)
        )
    ).scalar_one_or_none()
    if hospital_id is not None:
        await write_audit_log(
            db,
            action="post_publish_notification_sent",
            hospital_id=hospital_id,
            actor="notification-worker",
            target_type="content_item",
            target_id=identity.content_id,
            detail={"channel": "slack", "outbox_id": str(outbox_id)},
        )
    else:
        existing_stamp = (
            await db.execute(
                select(ContentItem.post_publish_notified_at).where(
                    ContentItem.id == identity.content_id,
                    ContentItem.status == ContentStatus.PUBLISHED,
                    ContentItem.published_at == identity.published_at,
                )
            )
        ).scalar_one_or_none()
        if existing_stamp != sent_at:
            return False
    if not await _recover_incident(db, outbox_id, sent_at):
        await db.rollback()
        raise PublishIncidentRecoveryPending(outbox_id)
    await write_audit_log(
        db,
        action="post_publish_notification_delivery_applied",
        hospital_id=outbox.hospital_id,
        actor="notification-worker",
        target_type="notification_outbox",
        target_id=outbox_id,
        detail={"content_id": str(identity.content_id)},
    )
    await db.commit()
    return True


async def _recover_incident(
    db: AsyncSession, outbox_id: uuid.UUID, sent_at: datetime
) -> bool:
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
