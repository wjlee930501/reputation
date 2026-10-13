"""Transactional enqueue, optimistic manual retry, and deterministic leases."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import String, and_, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.models.audit import AdminAuditLog
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    JSONValue,
    NotificationOutbox,
    NotificationOutboxState,
)
from app.services.audit_log import write_audit_log
from app.services.incident_safety import sanitize_operator_text
from app.services.incident_types import (
    QUIET_INCIDENT_TYPES,
    SWEEP_OWNED_INCIDENT_TYPES,
    IncidentFingerprint,
    IncidentOpenRequest,
    incident_is_quiet,
)
from app.services.incidents import open_or_touch_incident
from app.services.notification_contracts import (
    NotificationIntent,
    NotificationPayloadError,
    validate_message,
)

# `notification_outbox.fallback_text`는 String(1000)이다. 전송 payload(JSONB)는 전문을 그대로
# 싣고, 이 칸은 목록·검색용 미리보기라 길이에 맞춰 자른다. 자르지 않으면 긴 일일 요약이
# StringDataRightTruncation으로 저장 자체가 실패해 알림이 사라진다.
_FALLBACK_TEXT_COLUMN_LIMIT = 1000

# 사고가 알림 발송 전에 복구·확인됐다 — 보내지 않고 종결한다. 그 사고의 열림 알림이 하나도
# 나가지 않았다면 짝인 복구 알림도 보내지 않는다(나가지 않은 '조치 필요'에 '해결'은 필요 없다).
RESOLVED_BEFORE_SEND_CODE = "INCIDENT_RESOLVED_BEFORE_SEND"
_RESOLVED_BEFORE_SEND_MESSAGE = "사고가 알림 발송 전에 복구·확인되어 보내지 않았습니다."
_SENDABLE_STATES = (NotificationOutboxState.PENDING.value, NotificationOutboxState.RETRYING.value)
_MAY_HAVE_REACHED_STATES = (
    NotificationOutboxState.PENDING.value,
    NotificationOutboxState.SENDING.value,
    NotificationOutboxState.RETRYING.value,
    NotificationOutboxState.HOLD.value,
    NotificationOutboxState.SENT.value,
)


def _stored_fallback_text(text: str) -> str:
    if len(text) <= _FALLBACK_TEXT_COLUMN_LIMIT:
        return text
    return text[: _FALLBACK_TEXT_COLUMN_LIMIT - 1] + "…"


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


def _incident_type_stmt(incident_id: uuid.UUID):
    return select(Incident.incident_type).where(Incident.id == incident_id)


async def enqueue_notification(
    db: AsyncSession, intent: NotificationIntent, *, now: datetime | None = None
) -> NotificationOutbox | None:
    """Add one intent without committing the caller's business transaction.

    조용한 사고 종류(`incident_types.QUIET_INCIDENT_TYPES`)의 알림은 넣지 않고 None을 돌려준다 —
    모든 사고 알림이 이 입구를 지나므로 경로마다 막지 않아도 된다.
    """

    validate_message(intent.message, allowed_admin_base_url=settings.ADMIN_BASE_URL)
    if not intent.dedupe_key.strip() or intent.max_attempts < 1:
        raise NotificationPayloadError("INVALID_NOTIFICATION_INTENT")
    if intent.incident_id is not None and incident_is_quiet(
        (await db.execute(_incident_type_stmt(intent.incident_id))).scalar_one_or_none()
    ):
        return None
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
            fallback_text=_stored_fallback_text(intent.message.fallback_text),
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
    if intent.incident_id is not None and incident_is_quiet(
        db.execute(_incident_type_stmt(intent.incident_id)).scalar_one_or_none()
    ):
        return
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
            fallback_text=_stored_fallback_text(intent.message.fallback_text),
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
        # 행의 incident_id는 이 알림이 말하는 사고다. 수신 불명 사고는 source_id로 이 행을
        # 가리키므로 덮어쓰지 않는다 — 덮어쓰면 열림/복구 짝과 조용한 사고 필터가 어긋난다.
        await create_delivery_unknown_incident(
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


def claimable_notifications_stmt(*, claimed_at: datetime, limit: int):
    """보낼 차례인 알림.

    - 조용한 사고 종류에 묶인 행은 집지 않는다 — 입구에서 막기 전에 이미 쌓인 행(배포 전·배포 중
      생성분)도 보내지 않게 한다.
    - 발송 전 종결 대상(`settle_incident_notices`의 두 규칙)은 종결 배치가 아직 닿지 않았어도
      집지 않는다 — 종결 상한 밖의 행이 claim으로 새어 짝 없는 알림을 내보내지 않게 한다.
    - 스윕이 재시도를 소유하는 사고(`SWEEP_OWNED_INCIDENT_TYPES`)가 기한 안에서 RETRYING이면 그
      열림 알림을 집지 않는다. 기한이 지나면 그때 나간다 — 사람에게 넘어가는 순간의 알림이다.
      콘텐츠 생성처럼 등록하지 않은 종류는 RETRYING이어도 종전처럼 나간다(아침 알림 계약).
    """

    quiet_incident = (
        select(Incident.id)
        .where(
            Incident.id == NotificationOutbox.incident_id,
            func.upper(Incident.incident_type).in_(sorted(QUIET_INCIDENT_TYPES)),
        )
        .exists()
    )
    machine_owned = (
        select(Incident.id)
        .where(
            Incident.id == NotificationOutbox.incident_id,
            func.upper(Incident.incident_type).in_(sorted(SWEEP_OWNED_INCIDENT_TYPES)),
            Incident.state == IncidentState.RETRYING.value,
            Incident.sla_due_at > claimed_at,
        )
        .exists()
    )
    return (
        select(NotificationOutbox)
        .where(
            NotificationOutbox.state.in_(_SENDABLE_STATES),
            NotificationOutbox.next_attempt_at <= claimed_at,
            ~quiet_incident,
            ~and_(NotificationOutbox.notification_type == "INCIDENT_OPEN", machine_owned),
            # 종결 대상은 종결 배치(상한 200)가 이번 tick에 닿지 못했어도 집지 않는다.
            ~_open_settle_eligible(),
            ~_recovered_settle_eligible(),
        )
        .order_by(
            NotificationOutbox.next_attempt_at,
            NotificationOutbox.created_at,
            NotificationOutbox.id,
        )
        .with_for_update(skip_locked=True)
        .limit(max(1, min(limit, 100)))
    )


# 사고 알림 키의 에피소드 꼬리(`INCIDENT_OPEN:{id}:e{n}`). 옛 키에는 없다.
_EPISODE_SUFFIX = r":e([0-9]+)$"


def _same_episode_open(open_row):
    """이 복구 알림(`NotificationOutbox`)의 짝이 될 수 있는 열림 알림.

    복구 키에 에피소드가 있으면 같은 에피소드 키의 열림만 짝이다 — 다른 에피소드의 열림도, 에피소드
    없는 옛 열림도 아니다. 1회차(또는 옛 키)의 나간 열림이 2회차 복구를 내보내는 근거가 되면, 2회차
    열림이 발송 전에 종결됐는데도 채널에 짝 없는 복구 줄이 남는다. 에피소드가 없는 옛 복구 키만
    종전 규칙(복구보다 먼저 만든 열림)을 따른다.
    """
    recovered_episode = func.substring(NotificationOutbox.dedupe_key, _EPISODE_SUFFIX)
    open_episode = func.substring(open_row.dedupe_key, _EPISODE_SUFFIX)
    return or_(
        and_(recovered_episode.isnot(None), open_episode == recovered_episode),
        and_(
            recovered_episode.is_(None),
            open_row.created_at <= NotificationOutbox.created_at,
        ),
    )


async def settle_incident_notices(db: AsyncSession, *, now: datetime) -> None:
    """채널에 닿지 않을 짝을 종결한다(`INCIDENT_RESOLVED_BEFORE_SEND`).

    1. 열림 알림: 사고가 발송 전에 복구·확인됐거나, 사고가 그 뒤 다시 열려 지난 에피소드의 것이
       됐다(현재 에피소드 시작보다 먼저 만들었고 현재 에피소드 키가 아니다). 지난 에피소드의 '조치
       필요'를 지금 보내면 새 에피소드의 보류(스윕 소유 기한)를 앞질러 사람을 부른다.
    2. 복구 알림: 같은 에피소드의 열림 알림이 있었고 그중 채널에 닿았을 수 있는 것(PENDING·
       SENDING·RETRYING·HOLD·SENT)이 하나도 없다 — 모두 FAILED(발송 전 종결·전송 실패)다.
       다른 에피소드의 열림은 이 복구의 짝이 아니므로 세지 않는다(에피소드 없는 옛 키는
       복구보다 먼저 만든 열림을 짝으로 본다).
    짝 계약("나간 OPEN에는 반드시 RECOVERED가 따른다")은 그대로다.
    """

    settled = {
        "state": NotificationOutboxState.FAILED.value,
        "next_attempt_at": None,
        "safe_error_code": RESOLVED_BEFORE_SEND_CODE,
        "safe_error_message": _RESOLVED_BEFORE_SEND_MESSAGE,
        "version": NotificationOutbox.version + 1,
        "updated_at": now,
    }
    await _settle_locked(
        db,
        settled,
        NotificationOutbox.state.in_(_SENDABLE_STATES),
        _open_settle_eligible(),
    )
    await _settle_locked(
        db,
        settled,
        NotificationOutbox.state.in_(_SENDABLE_STATES),
        _recovered_settle_eligible(),
    )


def _open_settle_eligible():
    """규칙 1: 사고가 발송 전에 복구·확인됐거나 지난 에피소드의 것이 된 열림 알림."""

    current_episode_key = func.concat(
        "INCIDENT_OPEN:", cast(Incident.id, String), ":e", cast(Incident.episode_seq, String)
    )
    resolved_or_superseded = (
        select(Incident.id)
        .where(
            Incident.id == NotificationOutbox.incident_id,
            or_(
                Incident.state.in_(
                    (IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value)
                ),
                and_(
                    NotificationOutbox.created_at < Incident.first_seen_at,
                    NotificationOutbox.dedupe_key != current_episode_key,
                ),
            ),
        )
        .exists()
    )
    return and_(NotificationOutbox.notification_type == "INCIDENT_OPEN", resolved_or_superseded)


def _recovered_settle_eligible():
    """규칙 2: 같은 에피소드의 열림이 있었고 그중 채널에 닿았을 수 있는 것이 없는 복구 알림."""

    unreached = aliased(NotificationOutbox)
    reached = aliased(NotificationOutbox)
    open_never_reached = (
        select(unreached.id)
        .where(
            unreached.incident_id == NotificationOutbox.incident_id,
            unreached.notification_type == "INCIDENT_OPEN",
            unreached.state == NotificationOutboxState.FAILED.value,
            _same_episode_open(unreached),
        )
        .exists()
    )
    open_may_have_reached = (
        select(reached.id)
        .where(
            reached.incident_id == NotificationOutbox.incident_id,
            reached.notification_type == "INCIDENT_OPEN",
            reached.state.in_(_MAY_HAVE_REACHED_STATES),
            _same_episode_open(reached),
        )
        .exists()
    )
    return and_(
        NotificationOutbox.notification_type == "INCIDENT_RECOVERED",
        open_never_reached,
        ~open_may_have_reached,
    )


# 한 번의 claim이 종결하는 짝의 상한. 남은 것은 다음 tick(매분)이 마저 한다.
_SETTLE_BATCH_LIMIT = 200


async def _settle_locked(db: AsyncSession, settled: dict, *predicates) -> None:
    """종결 후보를 claim 트랜잭션 안에서 `FOR UPDATE SKIP LOCKED`로 잠근 뒤 id로 종결한다.

    무제한 UPDATE는 다른 트랜잭션(발송 중인 워커·수동 재시도)이 잡은 행 하나를 기다리느라 그 뒤의
    무관한 claim까지 멈춘다. 잠긴 후보는 이번 tick에 건너뛰고 다음 tick이 다시 본다.
    """

    candidate_ids = list(
        (
            await db.execute(
                select(NotificationOutbox.id)
                .where(*predicates)
                .order_by(NotificationOutbox.created_at, NotificationOutbox.id)
                .limit(_SETTLE_BATCH_LIMIT)
                .with_for_update(of=NotificationOutbox, skip_locked=True)
            )
        ).scalars()
    )
    if not candidate_ids:
        return
    await db.execute(
        update(NotificationOutbox)
        .where(NotificationOutbox.id.in_(candidate_ids))
        .values(**settled)
        .execution_options(synchronize_session=False)
    )


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
    await settle_incident_notices(db, now=claimed_at)
    rows = list(
        (await db.execute(claimable_notifications_stmt(claimed_at=claimed_at, limit=limit))).scalars()
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
