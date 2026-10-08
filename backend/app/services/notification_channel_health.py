"""Slack 채널(웹훅) 하나의 건강 상태 — 채널 단위 전송 사고를 그 상태의 정본으로 쓴다.

2026-09-19부터 개발 채널 웹훅이 302를 돌려줘 한 번도 전달되지 않았는데, 전송기는 이를
알림마다의 실패로만 다뤘다. 채널 사고는 죽은 그 채널로 자기 알림을 보내려 했고, 무엇도
다시 확인하지 않아 영원히 열려 있었다. 이제:

- 웹훅 주소 자체가 죽었다는 증거(`channel_is_dead`)를 보면 채널 사고 하나를 연다.
- 그 사고가 열려 있는 동안 그 채널의 알림은 다른 채널로 `[채널 대체 전송]` 표시와 함께 간다.
- 한 시간에 한 번 빈 본문으로 탐침하고, 살아 있으면 사고를 닫고 보류된 알림을 다시 보낸다.
  복구 직후(탐침 간격 안에) 다시 죽는 채널은 탐침 간격을 두 배로 늘리고(최대 6시간) 같은
  KST 날에는 열림/복구 알림을 다시 보내지 않는다.
- 보류 알림 중 이미 해결된 사고의 것이나 24시간이 지난 것은 다시 보내지 않는다(지난 사실).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import String, case, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.audit import AdminAuditLog
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    NotificationOutbox,
    NotificationOutboxState,
)
from app.services.audit_log import write_audit_log
from app.services.incident_types import (
    SLACK_CHANNEL,
    SLACK_DEVELOPER_CHANNEL,
    IncidentFingerprint,
    IncidentOpenRequest,
    incident_type_of,
)
from app.services.incidents import (
    auto_acknowledge_incident,
    build_incident_key,
    mark_recovered,
    mark_retrying,
    open_or_touch_incident,
)
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import (
    build_open_incident_notification,
    build_recovered_incident_notification,
)
from app.services.notification_store import enqueue_notification
from app.services.notification_transport import (
    CHANNEL_UNAVAILABLE_CODE,
    TransportDecision,
    probe_webhook,
    safe_error_message,
)

DELIVERY_INCIDENT_TYPES = frozenset({"NOTIFICATION_DELIVERY_FAILED", "NOTIFICATION_DELIVERY_UNKNOWN"})
CHANNEL_INCIDENT_TYPE = "NOTIFICATION_DELIVERY_FAILED"
CHANNELS = (SLACK_CHANNEL, SLACK_DEVELOPER_CHANNEL)
PROBE_INTERVAL = timedelta(hours=1)
MAX_PROBE_INTERVAL = timedelta(hours=6)
# 보낼 채널이 없어 보류한 알림을 이보다 오래 지난 뒤에는 다시 보내지 않는다 — 지난 사실이다.
HELD_RESEND_MAX_AGE = timedelta(hours=24)
STALE_CODE = "STALE_NOT_RESENT"
STALE_MESSAGE = "이미 해결된 사고이거나 오래된 알림이라 다시 보내지 않았습니다."
_FLAP_ACTION = "notification_channel_flap"
_KST = ZoneInfo("Asia/Seoul")
_ACTIVE = (IncidentState.OPEN.value, IncidentState.RETRYING.value)
_ACTOR = "notification-worker"
_HOSPITAL_LABEL = "시스템 공통 작업"


def outbox_row_of_incident(incident_type, incident_id, source_id):
    """사고가 가리키는 outbox 행의 조건(SQL).

    전송 사고는 실패한 그 행을 `source_id`(행 id)로 가리킨다 — 2026-10 이후 행의
    `incident_id`는 덮어쓰지 않으므로 '그 사고의 알림'이 아니라 '실패한 알림'을 찾으려면
    source_id로 맞춰야 한다. 채널 단위 사고(source_id=채널 이름)는 행 하나에 대응하지 않는다.
    그 밖의 사고는 그 사고를 알리는 행(`incident_id`)이다.
    """

    return case(
        (
            incident_type.in_(sorted(DELIVERY_INCIDENT_TYPES)),
            cast(NotificationOutbox.id, String) == source_id,
        ),
        else_=NotificationOutbox.incident_id == incident_id,
    )


def channel_incident_key(channel: str) -> str:
    return build_incident_key(
        "notification", "channel", channel, IncidentFingerprint.CONFIGURATION_ERROR
    )


def _channel_incident_query():
    return select(Incident).where(
        Incident.incident_type == CHANNEL_INCIDENT_TYPE,
        Incident.source_type == "NOTIFICATION_OUTBOX",
        Incident.source_id.in_(CHANNELS),
        Incident.state.in_(_ACTIVE),
    )


async def load_unhealthy_channels(db: AsyncSession) -> dict[str, Incident]:
    rows = (await db.scalars(_channel_incident_query().order_by(Incident.id))).all()
    return {str(row.source_id): row for row in rows}


async def open_channel_incident(
    db: AsyncSession, channel: str, decision: TransportDecision, now: datetime
) -> Incident:
    """채널 사고를 열거나 갱신한다. 새로 열리거나 다시 열릴 때만 사람에게 한 번 알린다."""

    previous = (
        await db.execute(
            select(Incident.state, Incident.recovered_at).where(
                Incident.dedupe_key == channel_incident_key(channel)
            )
        )
    ).first()
    previous_state = previous[0] if previous else None
    incident = await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="notification",
            object_type="channel",
            object_id=channel,
            fingerprint=IncidentFingerprint.CONFIGURATION_ERROR,
            incident_type=CHANNEL_INCIDENT_TYPE,
            severity=IncidentSeverity.HIGH,
            customer_impact="이 Slack 채널로 가는 운영 알림이 전달되지 않습니다. 다른 채널로 대신 보냅니다.",
            source_type="NOTIFICATION_OUTBOX",
            next_action="Slack 웹훅 주소를 확인해 주세요. 주소가 살아나면 한 시간 안에 자동으로 복구됩니다.",
            admin_path="/operations",
            source_id=channel,
            safe_error_code=decision.code,
            safe_error_message=safe_error_message(decision.code),
        ),
        actor=_ACTOR,
        reason="Slack webhook rejected every delivery",
        now=now,
    )
    reopened = previous_state in {IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value}
    if reopened and previous[1] is not None and now - previous[1] < PROBE_INTERVAL:
        await _record_flap(db, incident, now)
    if previous_state is None or (reopened and not await _open_notice_today(db, incident, now)):
        # 이 알림은 사고가 가리키는 채널을 피해 간다(`notification_delivery._resolve_route`).
        await enqueue_notification(
            db,
            build_open_incident_notification(_projection(incident), settings.ADMIN_BASE_URL),
            now=now,
        )
    return incident


async def recover_channel_incident(
    db: AsyncSession, incident: Incident, *, now: datetime, reason: str
) -> bool:
    """관측된 정상(탐침·전송 성공·단일 채널 전환)으로 채널 사고를 닫고 보류 알림을 다시 보낸다."""

    current: Incident | object = incident
    if incident.state == IncidentState.OPEN.value:
        current = await mark_retrying(
            db, incident.id, expected_version=incident.version, actor=_ACTOR, reason=reason
        )
    if not isinstance(current, Incident) or current.state != IncidentState.RETRYING.value:
        return False
    recovered = await mark_recovered(
        db,
        current.id,
        expected_version=current.version,
        observed_success=True,
        actor=_ACTOR,
        reason=reason,
        now=now,
    )
    if not isinstance(recovered, Incident):
        return False
    if await _open_notice_sent(db, recovered):
        await enqueue_notification(
            db,
            build_recovered_incident_notification(_projection(recovered), settings.ADMIN_BASE_URL),
            now=now,
        )
    await auto_acknowledge_incident(
        db, recovered.id, expected_version=recovered.version, actor=_ACTOR, reason=reason, now=now
    )
    await requeue_channel_held(db, now=now)
    return True


async def requeue_channel_held(db: AsyncSession, *, now: datetime) -> int:
    """보낼 채널이 없어 보류한 알림을 다시 보낼 차례로 돌린다(오래된 것부터).

    이 행들은 Slack에 닿지 않았으므로 다시 보내도 중복 전달이 없다. 다만 그 알림이 말하던
    사고가 이미 복구·확인됐거나 보류된 지 24시간이 지났으면 지난 사실이다 — 다시 보내지
    않고 종결(`STALE_NOT_RESENT`)한다. 여전히 보낼 곳이 없으면 다음 회차가 다시 보류한다.
    """

    resolved_subject = (
        select(Incident.id)
        .where(
            Incident.id == NotificationOutbox.incident_id,
            Incident.state.in_((IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value)),
        )
        .exists()
    )
    held = (
        NotificationOutbox.state == NotificationOutboxState.HOLD.value,
        NotificationOutbox.safe_error_code == CHANNEL_UNAVAILABLE_CODE,
    )
    await db.execute(
        update(NotificationOutbox)
        .where(
            *held,
            or_(resolved_subject, NotificationOutbox.created_at < now - HELD_RESEND_MAX_AGE),
        )
        .values(
            state=NotificationOutboxState.FAILED.value,
            next_attempt_at=None,
            safe_error_code=STALE_CODE,
            safe_error_message=STALE_MESSAGE,
            version=NotificationOutbox.version + 1,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.state == NotificationOutboxState.HOLD.value,
            NotificationOutbox.safe_error_code == CHANNEL_UNAVAILABLE_CODE,
        )
        .values(
            state=NotificationOutboxState.RETRYING.value,
            # created_at 순으로 보내지도록 같은 시각을 준다(claim은 next_attempt_at, created_at 순).
            next_attempt_at=now,
            attempt_count=0,
            safe_error_code=None,
            safe_error_message=None,
            version=NotificationOutbox.version + 1,
            updated_at=now,
        )
        .returning(NotificationOutbox.id)
    )
    return len(result.scalars().all())


async def refresh_channel_health(
    sessions: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    webhooks: Mapping[str, str],
    *,
    now: datetime,
) -> dict[str, uuid.UUID]:
    """열린 채널 사고를 확인하고, 여전히 죽은 채널과 그 사고 id를 돌려준다."""

    async with sessions() as db:
        unhealthy = await load_unhealthy_channels(db)
    remaining: dict[str, uuid.UUID] = {}
    for channel, incident in unhealthy.items():
        if channel == SLACK_DEVELOPER_CHANNEL and not webhooks.get(channel, "").strip():
            # 개발 채널 웹훅을 비웠다 = 운영 채널 하나로 운영한다. 죽은 주소를 더 쓰지 않는다.
            async with sessions() as db:
                await recover_channel_incident(
                    db, incident, now=now, reason="developer webhook removed; single-channel mode"
                )
                await db.commit()
            continue
        async with sessions() as db:
            interval = await probe_interval(db, incident, now)
        if now - incident.last_seen_at < interval:
            remaining[channel] = incident.id
            continue
        probe = await probe_webhook(client, webhooks.get(channel, ""))
        async with sessions() as db:
            if probe == "alive":
                await recover_channel_incident(db, incident, now=now, reason="webhook probe alive")
                await db.commit()
                continue
            # 죽음·모름 모두 다음 탐침을 한 간격 뒤로 미룬다(간격의 기준이 마지막 관측 시각이다).
            await db.execute(
                update(Incident)
                .where(Incident.id == incident.id, Incident.version == incident.version)
                .values(last_seen_at=now, updated_at=now, version=Incident.version + 1)
            )
            await db.commit()
        remaining[channel] = incident.id
    return remaining


async def recover_after_send(
    sessions: async_sessionmaker[AsyncSession], channel: str, *, now: datetime
) -> None:
    """그 채널로 실제 전송이 성공했다 — 열린 채널 사고가 있으면 닫는다."""

    async with sessions() as db:
        incident = (await load_unhealthy_channels(db)).get(channel)
        if incident is None:
            return
        await recover_channel_incident(db, incident, now=now, reason="Slack delivery observed")
        await db.commit()


async def described_channels(
    db: AsyncSession, incident_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """전송 사고 알림이 '어느 채널의 문제'를 말하는지. 그 채널로는 보내지 않는다."""

    if not incident_ids:
        return {}
    rows = (
        await db.execute(
            select(Incident.id, Incident.source_id).where(
                Incident.id.in_(incident_ids),
                Incident.incident_type.in_(sorted(DELIVERY_INCIDENT_TYPES)),
            )
        )
    ).all()
    described: dict[uuid.UUID, str] = {}
    outbox_sources: dict[uuid.UUID, uuid.UUID] = {}
    for incident_id, source_id in rows:
        if source_id in CHANNELS:
            described[incident_id] = str(source_id)
            continue
        try:
            outbox_sources[incident_id] = uuid.UUID(str(source_id))
        except ValueError:
            continue
    if outbox_sources:
        channels = dict(
            (
                await db.execute(
                    select(NotificationOutbox.id, NotificationOutbox.channel).where(
                        NotificationOutbox.id.in_(set(outbox_sources.values()))
                    )
                )
            ).all()
        )
        for incident_id, outbox_id in outbox_sources.items():
            if outbox_id in channels:
                described[incident_id] = str(channels[outbox_id])
    return described


async def probe_interval(db: AsyncSession, incident: Incident, now: datetime) -> timedelta:
    """다음 탐침까지의 간격. 하루 안에 깜빡인(복구 직후 다시 죽은) 기록이 있으면 그 간격."""

    detail = await db.scalar(
        select(AdminAuditLog.detail)
        .where(
            AdminAuditLog.action == _FLAP_ACTION,
            AdminAuditLog.target_id == str(incident.id),
            AdminAuditLog.created_at > now - timedelta(days=1),
        )
        .order_by(AdminAuditLog.created_at.desc())
        .limit(1)
    )
    seconds = (detail or {}).get("probe_interval_seconds")
    return timedelta(seconds=seconds) if isinstance(seconds, int) else PROBE_INTERVAL


async def _record_flap(db: AsyncSession, incident: Incident, now: datetime) -> None:
    # 복구 직후 다시 죽었다 — 다음 탐침 간격을 두 배로(최대 6시간). 감사 기록이 간격의 정본이다.
    doubled = min((await probe_interval(db, incident, now)) * 2, MAX_PROBE_INTERVAL)
    await write_audit_log(
        db,
        action=_FLAP_ACTION,
        actor=_ACTOR,
        target_type="incident",
        target_id=incident.id,
        detail={"probe_interval_seconds": int(doubled.total_seconds())},
    )


def _kst_day_start(now: datetime) -> datetime:
    return now.astimezone(_KST).replace(hour=0, minute=0, second=0, microsecond=0)


async def _open_notice_today(db: AsyncSession, incident: Incident, now: datetime) -> bool:
    """같은 KST 날에 이 채널 사고의 열림 알림을 이미 큐에 넣었다 — 깜빡임마다 다시 알리지 않는다."""

    count = await db.scalar(
        select(func.count(NotificationOutbox.id)).where(
            NotificationOutbox.incident_id == incident.id,
            NotificationOutbox.notification_type == "INCIDENT_OPEN",
            NotificationOutbox.created_at >= _kst_day_start(now),
        )
    )
    return bool(count)


async def _open_notice_sent(db: AsyncSession, incident: Incident) -> bool:
    """이번 episode의 열림 알림이 실제로 전달됐다. 전달되지 않은 열림에 복구를 짝짓지 않는다."""

    state = await db.scalar(
        select(NotificationOutbox.state).where(
            NotificationOutbox.dedupe_key == f"INCIDENT_OPEN:{incident.id}:e{incident.episode_seq}"
        )
    )
    return state == NotificationOutboxState.SENT.value


def _projection(incident: Incident) -> IncidentSlackProjection:
    return IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name=_HOSPITAL_LABEL,
        severity=incident.severity,
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        owner_label="미지정",
        sla_label="확인 필요",
        hospital_id=incident.hospital_id,
        operation_run_id=incident.operation_run_id,
        version=incident.version,
        problem=incident.safe_error_message or "Slack 웹훅이 알림을 받지 않습니다.",
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )
