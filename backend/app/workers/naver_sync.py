"""Weekly Naver blog evidence handoff with durable per-URL outcomes."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone

from celery import current_task
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.celery_app import celery_app
from app.core.database import get_async_sessionmaker
from app.models.hospital import Hospital, HospitalStatus
from app.services.naver_handoff import sync_hospital_naver_sources
from app.services.naver_handoff_contracts import NaverHandoffResult, NaverHandoffState
from app.services.naver_handoff_messages import NaverWeeklyEntry, build_naver_weekly_digest
from app.services.notification_outbox import enqueue_notification
from app.workers.dispatch_auth import require_dispatch

logger = logging.getLogger(__name__)

SYNC_TARGET_STATUSES = (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN)
NaverSyncResult = NaverHandoffResult
_tls = threading.local()


def _run_async(coroutine):
    """Reuse one event loop per Celery worker thread."""
    loop = getattr(_tls, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _tls.loop = loop
    return loop.run_until_complete(coroutine)


@celery_app.task(
    name="app.workers.naver_sync.weekly_naver_source_sync",
    soft_time_limit=1800,
    time_limit=2100,
)
def weekly_naver_source_sync() -> dict[str, int]:
    """Run the Tuesday handoff and return aggregate counters."""
    require_dispatch(current_task, "weekly-naver-source-sync")
    return _run_async(_weekly_naver_source_sync_async())


async def _weekly_naver_source_sync_async() -> dict[str, int]:
    processed = 0
    created_total = 0
    failures: list[str] = []
    entries: list[NaverWeeklyEntry] = []
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        hospitals = (
            await db.execute(
                select(Hospital).where(
                    Hospital.status.in_(SYNC_TARGET_STATUSES),
                    Hospital.blog_url.is_not(None),
                    Hospital.blog_url != "",
                )
            )
        ).scalars().all()
        for hospital in hospitals:
            try:
                result = await sync_hospital_naver_sources(db, hospital)
            except SQLAlchemyError:
                logger.exception("naver weekly sync database failure", extra={"hospital_id": hospital.id})
                failures.append(hospital.name)
                await db.rollback()
                continue
            processed += 1
            if result.error:
                logger.info(
                    "naver weekly sync requires operator review",
                    extra={"hospital_id": hospital.id, "operation_run_id": result.run_id},
                )
            created_total += result.created
            failed_count = sum(
                item.state == NaverHandoffState.FAILED for item in result.items
            ) + int(result.error is not None)
            entries.append(
                NaverWeeklyEntry(
                    hospital_name=hospital.name,
                    created=result.created,
                    requested=result.requested,
                    failed=failed_count,
                )
            )
        if entries:
            await enqueue_notification(
                db,
                build_naver_weekly_digest(
                    tuple(entries), datetime.now(timezone.utc).date()
                ),
            )
            await db.commit()
    logger.info(
        "weekly naver source handoff completed",
        extra={
            "processed_count": processed,
            "created_count": created_total,
            "database_failure_count": len(failures),
        },
    )
    return {"processed": processed, "created": created_total, "failed": len(failures)}
