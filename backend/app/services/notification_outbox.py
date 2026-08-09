"""Stable facade for durable Slack outbox callers."""

from app.services.notification_contracts import (
    IncidentSlackProjection,
    NotificationIntent,
    NotificationPayloadError,
    SlackMessage,
)
from app.services.notification_delivery import DispatchResult, dispatch_notification_batch
from app.services.notification_messages import (
    build_open_incident_notification,
    build_recovered_incident_notification,
    build_summary_notification,
)
from app.services.notification_store import (
    ClaimedNotification,
    NotificationRetryConflict,
    claim_notification_batch,
    enqueue_notification,
    recover_stale_sending,
    retry_notification,
)

__all__ = (
    "ClaimedNotification",
    "DispatchResult",
    "IncidentSlackProjection",
    "NotificationIntent",
    "NotificationPayloadError",
    "NotificationRetryConflict",
    "SlackMessage",
    "build_open_incident_notification",
    "build_recovered_incident_notification",
    "build_summary_notification",
    "claim_notification_batch",
    "dispatch_notification_batch",
    "enqueue_notification",
    "recover_stale_sending",
    "retry_notification",
)
