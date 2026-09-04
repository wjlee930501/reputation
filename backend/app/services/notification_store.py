"""Transactional enqueue, optimistic manual retry, and deterministic leases."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit import AdminAuditLog
from app.models.operations import (
    IncidentSeverity,
    JSONValue,
    NotificationOutbox,
    NotificationOutboxState,
)
from app.services.audit_log import write_audit_log
from app.services.incident_safety import sanitize_operator_text
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import open_or_touch_incident
from app.services.notification_contracts import (
    NotificationIntent,
    NotificationPayloadError,
    validate_message,
)


@dataclass(frozen=True, slots=True)
class ClaimedNotification:
    id: uuid.UUID
    hospital_id: uuid.UUID | None
    incident_id: uuid.UUID | None
    operation_run_id: uuid.UUID | None
    payload: dict[str, JSONValue]
    attempt_count: int
    max_attempts: int
    lease_owner: str
    version: int
    # Transport routing key. "SLACK" is the operator channel; "SLACK_DEV" carries
    # incidents whose registered audience is the developer.
    channel: str = "SLACK"


@dataclass(frozen=True, slots=True)
class NotificationRetryConflict:
    code: str
    notification_id: uuid.UUID
    expected_version: int
    current_version: int | None
    current_state: str | None


async def enqueue_notification(
    db: AsyncSession, intent: NotificationIntent, *, now: datetime | None = None
) -> NotificationOutbox:
    """Add one intent without committing the caller's business transaction."""

    validate_message(intent.message, allowed_admin_base_url=settings.ADMIN_BASE_URL)
    if not intent.dedupe_key.strip() or intent.max_attempts < 1:
        raise NotificationPayloadError("INVALID_NOTIFICATION_INTENT")
    created_at = now or datetime.now(UTC)
    statement = (
        insert(NotificationOutbox)
        .values(
            id=uuid.uuid4(),
            hospital_id=intent.hospital_id,
            incident_id=intent.incident_id,
            operation_run_id=intent.operation_run_id,
            dedupe_key=intent.dedupe_key,
            notification_type=intent.notification_type,
            channel=intent.channel,
            state=NotificationOutboxState.PENDING.value,
            payload=intent.message.payload(),
            fallback_text=intent.message.fallback_text,
            max_attempts=intent.max_attempts,
            next_attempt_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        .on_conflict_do_nothing(index_elements=[NotificationOutbox.dedupe_key])
        .returning(NotificationOutbox)
    )
    row = (await db.execute(statement)).scalar_one_or_none()
    if row is not None:
        return row
    existing = select(NotificationOutbox).where(NotificationOutbox.dedupe_key == intent.dedupe_key)
    return (await db.execute(existing)).scalar_one()


def enqueue_notification_sync(
    db: Session, intent: NotificationIntent, *, now: datetime | None = None
) -> None:
    """Add one intent to a synchronous business transaction without committing it."""

    validate_message(intent.message, allowed_admin_base_url=settings.ADMIN_BASE_URL)
    if not intent.dedupe_key.strip() or intent.max_attempts < 1:
        raise NotificationPayloadError("INVALID_NOTIFICATION_INTENT")
    created_at = now or datetime.now(UTC)
    db.execute(
        insert(NotificationOutbox)
        .values(
            id=uuid.uuid4(),
            hospital_id=intent.hospital_id,
            incident_id=intent.incident_id,
            operation_run_id=intent.operation_run_id,
            dedupe_key=intent.dedupe_key,
            notification_type=intent.notification_type,
            channel=intent.channel,
            state=NotificationOutboxState.PENDING.value,
            payload=intent.message.payload(),
            fallback_text=intent.message.fallback_text,
            max_attempts=intent.max_attempts,
            next_attempt_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        .on_conflict_do_nothing(index_elements=[NotificationOutbox.dedupe_key])
    )


async def retry_notification(
    db: AsyncSession,
    notification_id: uuid.UUID,
    *,
    expected_version: int,
    actor: str,
    reason: str,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> NotificationOutbox | NotificationRetryConflict:
    """CAS a HOLD/FAILED row to RETRYING; audited exact-version replay is compatible."""

    requested_at = now or datetime.now(UTC)
    row = (
        await db.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.id == notification_id,
                NotificationOutbox.version == expected_version,
                NotificationOutbox.state.in_(
                    (NotificationOutboxState.HOLD.value, NotificationOutboxState.FAILED.value)
                ),
            )
            .values(
                state=NotificationOutboxState.RETRYING.value,
                next_attempt_at=requested_at,
                lease_owner=None,
                lease_expires_at=None,
                provider_message_id=None,
                provider_response=None,
                safe_error_code=None,
                safe_error_message=None,
                sent_at=None,
                attempt_count=0,
                version=NotificationOutbox.version + 1,
                updated_at=requested_at,
            )
            .returning(NotificationOutbox)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is not None:
        await write_audit_log(
            db,
            action="notification_retry_requested",
            hospital_id=row.hospital_id,
            actor=actor,
            target_type="notification_outbox",
            target_id=row.id,
            detail={
                "actor_id": str(actor_id) if actor_id else None,
                "expected_version": expected_version,
                "reason": sanitize_operator_text(reason, limit=200),
                "state": row.state,
                "version": row.version,
            },
        )
        await db.flush()
        return row
    current_query = select(NotificationOutbox).where(NotificationOutbox.id == notification_id)
    current = await db.scalar(current_query.execution_options(populate_existing=True))
    replay = current is not None and (
        current.state == NotificationOutboxState.RETRYING.value
        and current.version == expected_version + 1
    )
    if replay:
        audit_exists = await db.scalar(
            select(func.count(AdminAuditLog.id)).where(
                AdminAuditLog.action == "notification_retry_requested",
                AdminAuditLog.target_id == str(notification_id),
                AdminAuditLog.detail["expected_version"].as_integer() == expected_version,
            )
        )
        if audit_exists:
            return current
    if current is None:
        return NotificationRetryConflict("NOTIFICATION_NOT_FOUND", notification_id, expected_version, None, None)
    code = (
        "NOTIFICATION_VERSION_CONFLICT"
        if current.version != expected_version
        else "NOTIFICATION_RETRY_STATE_CONFLICT"
    )
    return NotificationRetryConflict(code, notification_id, expected_version, current.version, current.state)


async def create_delivery_unknown_incident(
    db: AsyncSession,
    row_or_claim: NotificationOutbox | ClaimedNotification,
    *,
    now: datetime,
    actor: str,
    reason: str,
) -> uuid.UUID:
    """Open the operator reconciliation incident for an unknowable Slack delivery."""

    incident = await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="notification",
            object_type="outbox",
            object_id=str(row_or_claim.id),
            fingerprint=IncidentFingerprint.DELIVERY_OUTCOME_UNKNOWN,
            incident_type="NOTIFICATION_DELIVERY_UNKNOWN",
            severity=IncidentSeverity.HIGH,
            customer_impact=(
                "운영 알림이 Slack에 도착했는지 확인할 수 없습니다. "
                "중복 발송 가능성이 있어 자동 재시도하지 않습니다."
            ),
            source_type="NOTIFICATION_OUTBOX",
            next_action=(
                "Slack 채널에서 해당 알림 수신 여부를 확인한 뒤, 미수신이 확실할 때만 "
                "Admin에서 수동 재시도해 주세요."
            ),
            admin_path="/operations",
            hospital_id=row_or_claim.hospital_id,
            operation_run_id=row_or_claim.operation_run_id,
            source_id=str(row_or_claim.id),
            safe_error_code="DELIVERY_OUTCOME_UNKNOWN",
            safe_error_message="Slack 수신 여부가 불확실합니다. 중복 여부 확인 후 수동 재시도하세요.",
        ),
        actor=actor,
        reason=reason,
        now=now,
    )
    return incident.id


async def recover_stale_sending(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Hold expired leases because the remote delivery outcome is unknowable."""

    observed_at = now or datetime.now(UTC)
    stale_rows = tuple(
        (
            await db.execute(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.state == NotificationOutboxState.SENDING.value,
                    (
                        (NotificationOutbox.lease_expires_at <= observed_at)
                        | NotificationOutbox.lease_expires_at.is_(None)
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    recovered = 0
    for row in stale_rows:
        incident_id = await create_delivery_unknown_incident(
            db,
            row,
            now=observed_at,
            actor="notification-worker",
            reason="stale notification lease outcome unknown",
        )
        result = await db.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.id == row.id,
                NotificationOutbox.state == NotificationOutboxState.SENDING.value,
                NotificationOutbox.version == row.version,
            )
            .values(
                state=NotificationOutboxState.HOLD.value,
                incident_id=incident_id,
                lease_owner=None,
                lease_expires_at=None,
                next_attempt_at=None,
                safe_error_code="DELIVERY_OUTCOME_UNKNOWN",
                safe_error_message="Slack 수신 여부를 확인한 뒤 수동으로 재시도해 주세요.",
                version=NotificationOutbox.version + 1,
                updated_at=observed_at,
            )
            .returning(NotificationOutbox.id)
        )
        recovered += int(result.scalar_one_or_none() is not None)
    await db.commit()
    return recovered


async def claim_notification_batch(
    db: AsyncSession,
    worker_id: str,
    *,
    now: datetime | None = None,
    limit: int = 50,
    lease_seconds: int = 120,
) -> tuple[ClaimedNotification, ...]:
    """Lease a deterministic due batch and commit before any transport I/O.

    Ordering caveat (accepted, not a bug to route around): the batch is ordered by
    `next_attempt_at` first, and a transient send failure pushes that row's next
    attempt into the future. So an `INCIDENT_OPEN` that failed once can land in the
    channel *after* the `INCIDENT_RECOVERED` that was enqueued later — during the
    retry backoff window the two Slack lines can read out of order. Both still
    arrive, which is the invariant that matters (a delivered OPEN is always closed);
    strict per-incident ordering would need a head-of-line lock per incident and
    would stall every other notification behind one failing row. The messages carry
    their own status text ("운영 확인 필요" / "자동 복구 완료"), so a reader is never
    left guessing which state is current.
    """

    claimed_at = now or datetime.now(UTC)
    rows = list(
        (
            await db.execute(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.state.in_(("PENDING", "RETRYING")),
                    NotificationOutbox.next_attempt_at <= claimed_at,
                )
                .order_by(
                    NotificationOutbox.next_attempt_at,
                    NotificationOutbox.created_at,
                    NotificationOutbox.id,
                )
                .with_for_update(skip_locked=True)
                .limit(max(1, min(limit, 100)))
            )
        ).scalars()
    )
    snapshots: list[ClaimedNotification] = []
    for row in rows:
        row.state = NotificationOutboxState.SENDING.value
        row.attempt_count += 1
        row.next_attempt_at = None
        row.lease_owner = worker_id
        row.lease_expires_at = claimed_at + timedelta(seconds=max(30, lease_seconds))
        row.version += 1
        row.updated_at = claimed_at
        snapshots.append(
            ClaimedNotification(
                row.id,
                row.hospital_id,
                row.incident_id,
                row.operation_run_id,
                row.payload,
                row.attempt_count,
                row.max_attempts,
                worker_id,
                row.version,
                row.channel,
            )
        )
    await db.commit()
    return tuple(snapshots)
