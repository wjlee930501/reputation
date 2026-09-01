"""Celery entry point for the leased notification outbox dispatcher."""

from __future__ import annotations

import uuid

import anyio
import httpx
from celery.app.task import Task

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.services.content_publish_reconciliation import (
    reconcile_sent_publish_notifications,
)
from app.services.notification_outbox import DispatchResult, dispatch_notification_batch
from app.services.notification_success_hooks import reconcile_sent_notification_incidents


async def _dispatch_once(worker_id: str) -> tuple[DispatchResult, int, int]:
    limits = httpx.Limits(
        max_connections=20,
        max_keepalive_connections=10,
        keepalive_expiry=30,
    )
    timeout = httpx.Timeout(connect=5, read=10, write=5, pool=5)
    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        sessions = get_async_sessionmaker()
        result = await dispatch_notification_batch(
            sessions,
            client,
            webhook_url=settings.SLACK_WEBHOOK_URL,
            developer_webhook_url=settings.SLACK_WEBHOOK_URL_DEV,
            worker_id=worker_id,
        )
    # Slack SENT is committed before domain hooks by design. Reapply any publish
    # projection whose worker died between those two commits.
    reconciled = await reconcile_sent_publish_notifications(sessions)
    incidents_recovered = await reconcile_sent_notification_incidents(sessions)
    return result, reconciled, incidents_recovered


@celery_app.task(name="app.workers.notification_tasks.dispatch_notification_outbox", bind=True)
def dispatch_notification_outbox(task: Task) -> dict[str, int]:
    """Drain one due batch; Beat invokes this every minute for durable recovery."""

    request_id = str(getattr(task.request, "id", None) or uuid.uuid4())
    hostname = str(getattr(task.request, "hostname", None) or "notification-worker")
    result, publish_reconciled, incidents_recovered = anyio.run(
        _dispatch_once, f"{hostname}:{request_id}"
    )
    return {
        "claimed": result.claimed,
        "sent": result.sent,
        "retried": result.retried,
        "held": result.held,
        "failed": result.failed,
        "stale": result.stale,
        "publish_reconciled": publish_reconciled,
        "incidents_recovered": incidents_recovered,
    }
