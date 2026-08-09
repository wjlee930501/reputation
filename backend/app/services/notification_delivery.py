"""Claimed outbox batch orchestration and lease-owner/version finalization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import assert_never

import anyio
import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.operations import IncidentSeverity, NotificationOutbox, NotificationOutboxState
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import open_or_touch_incident
from app.services.notification_store import (
    ClaimedNotification,
    claim_notification_batch,
    recover_stale_sending,
)
from app.services.notification_transport import (
    TransportDecision,
    deliver_once,
    retry_delay,
    safe_error_message,
)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    claimed: int = 0
    sent: int = 0
    retried: int = 0
    held: int = 0
    failed: int = 0
    stale: int = 0


async def dispatch_notification_batch(
    sessions: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    *,
    webhook_url: str,
    worker_id: str,
    now: datetime | None = None,
    limit: int = 20,
    throttle: Callable[[], Awaitable[None]] | None = None,
) -> DispatchResult:
    """Recover, claim, send once per row, throttle, and CAS-finalize."""

    dispatch_at = now or datetime.now(UTC)
    async with sessions() as stale_db:
        await recover_stale_sending(stale_db, now=dispatch_at)
    batch_limit = max(1, min(limit, 20))
    async with sessions() as claim_db:
        claimed = await claim_notification_batch(
            claim_db,
            worker_id,
            now=dispatch_at,
            limit=batch_limit,
            lease_seconds=(batch_limit * 25) + 60,
        )
    pause = throttle or _default_throttle
    counts = {state: 0 for state in ("sent", "retried", "held", "failed", "stale")}
    for index, row in enumerate(claimed):
        decision = await deliver_once(client, webhook_url, row.payload, dispatch_at)
        if decision.state == NotificationOutboxState.RETRYING and row.attempt_count >= row.max_attempts:
            decision = TransportDecision(
                NotificationOutboxState.FAILED,
                "DELIVERY_RETRY_EXHAUSTED",
                decision.provider_response,
                attempted=decision.attempted,
            )
        async with sessions() as finalize_db:
            finalized = await _finalize(finalize_db, row, decision, dispatch_at)
        if not finalized:
            counts["stale"] += 1
        else:
            _increment(counts, decision.state)
        if decision.attempted and index < len(claimed) - 1:
            await pause()
    return DispatchResult(claimed=len(claimed), **counts)


async def _finalize(
    db: AsyncSession,
    claimed: ClaimedNotification,
    decision: TransportDecision,
    now: datetime,
) -> bool:
    next_attempt = (
        now + timedelta(seconds=retry_delay(claimed.attempt_count, decision.retry_after_seconds))
        if decision.state == NotificationOutboxState.RETRYING
        else None
    )
    result = await db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id == claimed.id,
            NotificationOutbox.state == NotificationOutboxState.SENDING.value,
            NotificationOutbox.lease_owner == claimed.lease_owner,
            NotificationOutbox.version == claimed.version,
        )
        .values(
            state=decision.state.value,
            next_attempt_at=next_attempt,
            lease_owner=None,
            lease_expires_at=None,
            provider_message_id=None,
            provider_response=decision.provider_response,
            safe_error_code=decision.code,
            safe_error_message=safe_error_message(decision.code),
            sent_at=now if decision.state == NotificationOutboxState.SENT else None,
            version=NotificationOutbox.version + 1,
            updated_at=now,
        )
        .returning(NotificationOutbox.id)
    )
    finalized = result.scalar_one_or_none() is not None
    if finalized and decision.state == NotificationOutboxState.FAILED:
        fingerprint = (
            IncidentFingerprint.CONFIGURATION_ERROR
            if decision.code in {
                "WEBHOOK_NOT_CONFIGURED",
                "WEBHOOK_URL_REJECTED",
                "SLACK_PERMANENT_ERROR",
            }
            else IncidentFingerprint.DELIVERY_FAILED
        )
        await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline="notification",
                object_type="outbox",
                object_id=str(claimed.id),
                fingerprint=fingerprint,
                incident_type="NOTIFICATION_DELIVERY_FAILED",
                severity=IncidentSeverity.HIGH,
                customer_impact="운영 알림이 Slack에 전달되지 않았습니다.",
                source_type="NOTIFICATION_OUTBOX",
                next_action="Slack 설정을 확인한 뒤 알림을 수동 재시도해 주세요.",
                admin_path="/operations",
                hospital_id=claimed.hospital_id,
                operation_run_id=claimed.operation_run_id,
                source_id=str(claimed.id),
                safe_error_code=decision.code,
                safe_error_message=safe_error_message(decision.code),
            ),
            actor="notification-worker",
            reason="notification delivery became terminal",
            now=now,
        )
    await db.commit()
    return finalized


def _increment(counts: dict[str, int], state: NotificationOutboxState) -> None:
    match state:
        case NotificationOutboxState.SENT:
            counts["sent"] += 1
        case NotificationOutboxState.RETRYING:
            counts["retried"] += 1
        case NotificationOutboxState.HOLD:
            counts["held"] += 1
        case NotificationOutboxState.FAILED:
            counts["failed"] += 1
        case unreachable:
            assert_never(unreachable)


async def _default_throttle() -> None:
    await anyio.sleep(1)
