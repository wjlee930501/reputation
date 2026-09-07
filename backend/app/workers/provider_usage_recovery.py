"""Quietly replay bounded provider-usage telemetry deferred after DB failures."""

from __future__ import annotations

import asyncio

from celery import current_task

from app.core.celery_app import celery_app
from app.services.provider_usage import replay_deferred_attempts
from app.workers.dispatch_auth import require_dispatch


@celery_app.task(
    name="app.workers.provider_usage_recovery.drain",
    soft_time_limit=45,
    time_limit=55,
)
def drain() -> dict[str, int]:
    require_dispatch(current_task, "drain-provider-usage-spool")
    return asyncio.run(replay_deferred_attempts(limit=100))
