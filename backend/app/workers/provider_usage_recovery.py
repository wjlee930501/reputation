"""Quietly replay bounded provider-usage telemetry deferred after DB failures."""

from __future__ import annotations

import asyncio

from celery import current_task

from app.core.celery_app import celery_app
from app.services.provider_usage import replay_deferred_attempts, report_dropped_usage
from app.workers.dispatch_auth import require_dispatch


async def _drain_once() -> dict[str, int]:
    result = await replay_deferred_attempts(limit=100)
    # DB가 돌아온 뒤에만 의미가 있다 — 버린 기록 수를 개발 사고로 바꾼다(반환값에는 넣지 않는다).
    await report_dropped_usage()
    return result


@celery_app.task(
    name="app.workers.provider_usage_recovery.drain",
    soft_time_limit=45,
    time_limit=55,
)
def drain() -> dict[str, int]:
    require_dispatch(current_task, "drain-provider-usage-spool")
    return asyncio.run(_drain_once())
