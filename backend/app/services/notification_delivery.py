"""Claimed outbox batch orchestration and lease-owner/version finalization."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import assert_never

import anyio
import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.operations import IncidentSeverity, NotificationOutbox, NotificationOutboxState
from app.services.incident_types import (
    SLACK_DEVELOPER_CHANNEL,
    IncidentFingerprint,
    IncidentOpenRequest,
)
from app.services.incidents import open_or_touch_incident
from app.services.notification_store import (
    ClaimedNotification,
    claim_notification_batch,
    create_delivery_unknown_incident,
    recover_stale_sending,
)
from app.services.notification_success_hooks import run_notification_success_hook
from app.services.notification_transport import (
    TransportDecision,
    deliver_once,
    retry_delay,
    safe_error_message,
)

logger = logging.getLogger(__name__)


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
    developer_webhook_url: str = "",
    worker_id: str,
    now: datetime | None = None,
    limit: int = 20,
    throttle: Callable[[], Awaitable[None]] | None = None,
) -> DispatchResult:
    """Recover, claim, send once per row, throttle, and CAS-finalize.

    Rows on the developer channel go to ``developer_webhook_url`` when one is
    configured. With no developer webhook the routing collapses back to the single
    operator webhook, so an unset setting changes nothing.
    """

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
        target_url = _webhook_for(row, webhook_url, developer_webhook_url)
        decision = await deliver_once(client, target_url, row.payload, dispatch_at)
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
            if decision.state == NotificationOutboxState.SENT:
                # SENT is committed before domain stamping. A hook failure can delay the
                # local projection, but can never roll Slack truth back to a sendable state.
                async with sessions() as hook_db:
                    try:
                        await run_notification_success_hook(hook_db, row.id, dispatch_at)
                    except Exception:
                        logger.exception(
                            "Notification success projection deferred for outbox_id=%s",
                            row.id,
                        )
        if decision.attempted and index < len(claimed) - 1:
            await pause()
    return DispatchResult(claimed=len(claimed), **counts)


def _webhook_for(
    claimed: ClaimedNotification, operator_webhook_url: str, developer_webhook_url: str
) -> str:
    """Pick the webhook for one claimed row without ever dropping a notification."""

    if claimed.channel == SLACK_DEVELOPER_CHANNEL and developer_webhook_url:
        return developer_webhook_url
    return operator_webhook_url


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
    if finalized and decision.state == NotificationOutboxState.HOLD:
        incident_id = await create_delivery_unknown_incident(
            db,
            claimed,
            now=now,
            actor="notification-worker",
            reason="notification delivery outcome unknown",
        )
        await db.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.id == claimed.id)
            .values(incident_id=incident_id, updated_at=now)
        )
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
        incident = await open_or_touch_incident(
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
        await db.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.id == claimed.id)
            .values(incident_id=incident.id, updated_at=now)
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
