"""Claimed outbox batch orchestration and lease-owner/version finalization."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import assert_never

import anyio
import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.operations import IncidentSeverity, NotificationOutbox, NotificationOutboxState
from app.services.incident_types import (
    SLACK_CHANNEL,
    SLACK_DEVELOPER_CHANNEL,
    IncidentFingerprint,
    IncidentOpenRequest,
)
from app.services.incidents import open_or_touch_incident
from app.services.notification_channel_health import (
    described_channels,
    open_channel_incident,
    recover_after_send,
    refresh_channel_health,
    requeue_channel_held,
)
from app.services.notification_messages import (
    CHANNEL_FALLBACK_MARKER,
    DEVELOPER_ROUTED_MARKER,
    payload_with_routing_marker,
)
from app.services.notification_store import (
    ClaimedNotification,
    claim_notification_batch,
    create_delivery_unknown_incident,
    recover_stale_sending,
)
from app.services.notification_success_hooks import run_notification_success_hook
from app.services.notification_transport import (
    CHANNEL_UNAVAILABLE_CODE,
    TransportDecision,
    channel_is_dead,
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


@dataclass(frozen=True, slots=True)
class _Route:
    """한 행이 실제로 갈 곳. url이 None이면 보낼 수 있는 채널이 하나도 없다."""

    url: str | None
    channel: str
    marker: str | None = None


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
    """Recover, probe dead channels, claim, send once per row, throttle, and CAS-finalize.

    개발 채널 웹훅이 비어 있으면 운영 채널 하나로 운영한다 — 개발 담당 알림은 운영 채널로
    `[개발 확인]` 표시와 함께 간다(2026-10 대표 결정). 웹훅이 설정돼 있는데 죽었으면 채널
    사고(`notification_channel_health`)가 열리고, 열려 있는 동안 다른 채널로 대신 보낸다.
    """

    dispatch_at = now or datetime.now(UTC)
    webhooks = {
        SLACK_CHANNEL: webhook_url.strip(),
        SLACK_DEVELOPER_CHANNEL: developer_webhook_url.strip(),
    }
    async with sessions() as stale_db:
        await recover_stale_sending(stale_db, now=dispatch_at)
    unhealthy = set(await refresh_channel_health(sessions, client, webhooks, now=dispatch_at))
    if not unhealthy:
        # 모든 채널이 살아 있다 — 보낼 곳이 없어 보류했던 알림(수신 불명 정리로 옮겨진 것 포함)을
        # 다시 보내거나 지난 것은 종결한다. 채널이 죽어 있는 동안에는 돌리지 않는다.
        async with sessions() as requeue_db:
            await requeue_channel_held(requeue_db, now=dispatch_at)
            await requeue_db.commit()
    batch_limit = max(1, min(limit, 20))
    async with sessions() as claim_db:
        claimed = await claim_notification_batch(
            claim_db,
            worker_id,
            now=dispatch_at,
            limit=batch_limit,
            lease_seconds=(batch_limit * 25) + 60,
        )
        described = await described_channels(
            claim_db, {row.incident_id for row in claimed if row.incident_id is not None}
        )
    pause = throttle or _default_throttle
    counts = {state: 0 for state in ("sent", "retried", "held", "failed", "stale")}
    sent_channels: set[str] = set()
    for index, row in enumerate(claimed):
        route = _resolve_route(
            row.channel,
            described.get(row.incident_id) if row.incident_id is not None else None,
            webhooks,
            unhealthy,
        )
        decision = await _send(client, row, route, dispatch_at)
        dead = channel_is_dead(decision)
        if dead:
            # 주소가 죽었다는 증거다. 메시지는 전달되지 않았으므로 버리지 않는다 — 다른 채널이
            # 있으면 다음 회차에 그쪽으로, 없으면 채널이 복구될 때까지 보류한다.
            unhealthy.add(route.channel)
            decision = _after_dead_channel(decision, row.channel, webhooks, unhealthy)
        elif decision.state == NotificationOutboxState.RETRYING and row.attempt_count >= row.max_attempts:
            decision = TransportDecision(
                NotificationOutboxState.FAILED,
                "DELIVERY_RETRY_EXHAUSTED",
                decision.provider_response,
                attempted=decision.attempted,
            )
        async with sessions() as finalize_db:
            finalized = await _finalize(
                finalize_db, row, decision, dispatch_at, dead_channel=route.channel if dead else None
            )
        if not finalized:
            counts["stale"] += 1
        else:
            _increment(counts, decision.state)
            if decision.state == NotificationOutboxState.SENT:
                sent_channels.add(route.channel)
                await _run_success_hook(sessions, row, dispatch_at)
        if decision.attempted and index < len(claimed) - 1:
            await pause()
    for channel in sorted(sent_channels - unhealthy):
        # 이 채널로 실제 전달이 관측됐다 — 그 채널의 사고가 열려 있었다면 닫는다.
        await recover_after_send(sessions, channel, now=dispatch_at)
    return DispatchResult(claimed=len(claimed), **counts)


async def _send(
    client: httpx.AsyncClient, row: ClaimedNotification, route: _Route, now: datetime
) -> TransportDecision:
    if route.url is None:
        logger.error(
            "No live Slack channel; holding notification outbox_id=%s channel=%s",
            row.id,
            route.channel,
        )
        return TransportDecision(NotificationOutboxState.HOLD, CHANNEL_UNAVAILABLE_CODE, None)
    payload = payload_with_routing_marker(row.payload, route.marker) if route.marker else row.payload
    decision = await deliver_once(client, route.url, payload, now)
    if not decision.attempted:
        return decision
    # 실제로 보낸 채널을 남긴다 — 나중에 '그 채널이 죽어 있던 동안의 수신 불명'을 판정할 때
    # 논리 채널(row.channel)이 아니라 이 값으로 맞춘다(`incident_backlog`).
    return replace(
        decision, provider_response={**(decision.provider_response or {}), "channel_used": route.channel}
    )


async def _run_success_hook(
    sessions: async_sessionmaker[AsyncSession], row: ClaimedNotification, now: datetime
) -> None:
    # SENT is committed before domain stamping. A hook failure can delay the
    # local projection, but can never roll Slack truth back to a sendable state.
    async with sessions() as hook_db:
        try:
            await run_notification_success_hook(hook_db, row.id, now)
        except Exception:
            logger.exception("Notification success projection deferred for outbox_id=%s", row.id)


def _physical_channel(logical: str, webhooks: dict[str, str]) -> tuple[str, str | None]:
    """행의 수신 대상을 실제 웹훅 채널로 바꾼다. 개발 웹훅이 없으면 운영 채널 + `[개발 확인]`."""

    if logical == SLACK_DEVELOPER_CHANNEL:
        if webhooks.get(SLACK_DEVELOPER_CHANNEL):
            return SLACK_DEVELOPER_CHANNEL, None
        return SLACK_CHANNEL, DEVELOPER_ROUTED_MARKER
    return SLACK_CHANNEL, None


def _alternate(channel: str, webhooks: dict[str, str], unhealthy: set[str]) -> str | None:
    other = SLACK_DEVELOPER_CHANNEL if channel == SLACK_CHANNEL else SLACK_CHANNEL
    return other if webhooks.get(other) and other not in unhealthy else None


def _resolve_route(
    logical: str,
    describes: str | None,
    webhooks: dict[str, str],
    unhealthy: set[str],
) -> _Route:
    """한 행의 전송 경로. 전송 사고의 알림은 그 사고가 가리키는 채널로 보내지 않는다."""

    primary, marker = _physical_channel(logical, webhooks)
    alternate = _alternate(primary, webhooks, unhealthy)
    if primary in unhealthy:
        if alternate is None:
            return _Route(None, primary)
        return _Route(webhooks[alternate], alternate, CHANNEL_FALLBACK_MARKER)
    if describes is not None and alternate and _physical_channel(describes, webhooks)[0] == primary:
        return _Route(webhooks[alternate], alternate, CHANNEL_FALLBACK_MARKER)
    return _Route(webhooks[primary], primary, marker)


def _after_dead_channel(
    decision: TransportDecision,
    logical: str,
    webhooks: dict[str, str],
    unhealthy: set[str],
) -> TransportDecision:
    if _resolve_route(logical, None, webhooks, unhealthy).url is None:
        return TransportDecision(
            NotificationOutboxState.HOLD,
            CHANNEL_UNAVAILABLE_CODE,
            decision.provider_response,
            attempted=decision.attempted,
        )
    return TransportDecision(
        NotificationOutboxState.RETRYING,
        decision.code,
        decision.provider_response,
        retry_after_seconds=1,
        attempted=decision.attempted,
    )


async def _finalize(
    db: AsyncSession,
    claimed: ClaimedNotification,
    decision: TransportDecision,
    now: datetime,
    *,
    dead_channel: str | None = None,
) -> bool:
    """행을 CAS로 마무리한다. `dead_channel`이 있으면 그 웹훅이 죽었다 — 채널 사고를 연다.

    행의 `incident_id`는 바꾸지 않는다. 그 값은 이 알림이 말하는 사고이며 열림/복구 짝과
    조용한 사고 필터가 그것으로 맞춘다. 전송 사고는 `source_id`로 이 행(또는 채널)을 가리킨다.
    """

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
    if finalized and dead_channel is not None:
        await open_channel_incident(db, dead_channel, decision, now)
    elif (
        finalized
        and decision.state == NotificationOutboxState.HOLD
        and decision.code == "DELIVERY_OUTCOME_UNKNOWN"
    ):
        await create_delivery_unknown_incident(
            db,
            claimed,
            now=now,
            actor="notification-worker",
            reason="notification delivery outcome unknown",
        )
    elif finalized and decision.state == NotificationOutboxState.FAILED:
        await _open_row_delivery_incident(db, claimed, decision, now)
    await db.commit()
    return finalized


async def _open_row_delivery_incident(
    db: AsyncSession, claimed: ClaimedNotification, decision: TransportDecision, now: datetime
) -> None:
    # 메시지 하나가 거절됐거나 재시도를 다 쓴 일이다. 채널 전체의 문제는 채널 사고가 맡는다.
    await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="notification",
            object_type="outbox",
            object_id=str(claimed.id),
            fingerprint=(
                IncidentFingerprint.CONFIGURATION_ERROR
                if decision.code == "SLACK_PERMANENT_ERROR"
                else IncidentFingerprint.DELIVERY_FAILED
            ),
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
