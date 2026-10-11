"""발송기는 기한 안의 스윕 소유 사고와 이미 끝난 사고의 알림을 보내지 않는다(Phase 1-4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.operations import Incident, NotificationOutbox
from app.services.notification_store import (
    RESOLVED_BEFORE_SEND_CODE,
    claim_notification_batch,
)

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


def _incident(incident_type: str, state: str, *, sla: datetime | None = None) -> Incident:
    closed = state in ("RECOVERED", "ACKNOWLEDGED")
    return Incident(
        dedupe_key=f"notice-test:{uuid.uuid4()}",
        incident_type=incident_type,
        state=state,
        severity="HIGH",
        customer_impact="테스트",
        source_type="TEST",
        next_action="테스트",
        admin_path="/operations",
        sla_due_at=sla,
        first_seen_at=NOW - timedelta(hours=1),
        last_seen_at=NOW - timedelta(hours=1),
        recovered_at=NOW - timedelta(minutes=30) if closed else None,
        acknowledged_at=NOW - timedelta(minutes=10) if state == "ACKNOWLEDGED" else None,
    )


def _notice(
    incident: Incident,
    kind: str = "INCIDENT_OPEN",
    state: str = "PENDING",
    *,
    episode: int = 1,
    created_at: datetime | None = None,
    safe_error_code: str | None = None,
):
    pending = state in ("PENDING", "RETRYING")
    # 실제 알림처럼 에피소드 키를 쓰고, 에피소드 시작(first_seen_at) 뒤에 만든다.
    created = created_at or NOW - timedelta(minutes=30)
    return NotificationOutbox(
        dedupe_key=f"{kind}:{incident.id}:e{episode}",
        notification_type=kind,
        channel="SLACK",
        state=state,
        incident_id=incident.id,
        payload={},
        fallback_text="테스트",
        next_attempt_at=NOW - timedelta(minutes=5) if pending else None,
        sent_at=NOW - timedelta(minutes=20) if state == "SENT" else None,
        safe_error_code=safe_error_code,
        created_at=created,
        updated_at=created,
    )


async def _seed(db, incident: Incident, *notices_spec: tuple[str, str]):
    db.add(incident)
    await db.flush()
    rows = [_notice(incident, kind, state) for kind, state in notices_spec]
    db.add_all(rows)
    await db.flush()
    return rows


async def _row(db, row: NotificationOutbox) -> NotificationOutbox:
    return await db.scalar(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == row.id)
        .execution_options(populate_existing=True)
    )


async def _claimed_ids(db) -> set[uuid.UUID]:
    return {claim.id for claim in await claim_notification_batch(db, "test-worker", now=NOW)}


async def test_open_notice_waits_while_the_sweep_still_owns_the_incident(pg_async_session):
    db = pg_async_session
    owned = _incident("MONTHLY_SLOT_GENERATION_FAILED", "RETRYING", sla=NOW + timedelta(hours=5))
    (notice,) = await _seed(db, owned, ("INCIDENT_OPEN", "PENDING"))

    assert notice.id not in await _claimed_ids(db)
    assert (await _row(db, notice)).state == "PENDING"


async def test_open_notice_goes_out_once_the_window_has_passed(pg_async_session):
    db = pg_async_session
    overdue = _incident("MONTHLY_SLOT_GENERATION_FAILED", "RETRYING", sla=NOW - timedelta(minutes=1))
    (notice,) = await _seed(db, overdue, ("INCIDENT_OPEN", "PENDING"))

    assert notice.id in await _claimed_ids(db)


async def test_generation_incidents_keep_their_morning_page(pg_async_session):
    db = pg_async_session
    generation = _incident("CONTENT_GENERATION_FAILED", "RETRYING", sla=NOW + timedelta(hours=5))
    (notice,) = await _seed(db, generation, ("INCIDENT_OPEN", "PENDING"))

    assert notice.id in await _claimed_ids(db)


async def test_notices_of_an_incident_closed_before_sending_are_settled(pg_async_session):
    db = pg_async_session
    closed = _incident("MONTHLY_SLOT_GENERATION_FAILED", "ACKNOWLEDGED")
    opened, recovered = await _seed(
        db, closed, ("INCIDENT_OPEN", "PENDING"), ("INCIDENT_RECOVERED", "PENDING")
    )

    claimed = await _claimed_ids(db)

    assert opened.id not in claimed and recovered.id not in claimed
    for row in (await _row(db, opened), await _row(db, recovered)):
        assert row.state == "FAILED"
        assert row.safe_error_code == RESOLVED_BEFORE_SEND_CODE
        assert row.next_attempt_at is None


async def test_a_recovery_follows_an_open_that_already_reached_slack(pg_async_session):
    db = pg_async_session
    recovered_incident = _incident("CONTENT_GENERATION_FAILED", "RECOVERED")
    _sent, recovered = await _seed(
        db, recovered_incident, ("INCIDENT_OPEN", "SENT"), ("INCIDENT_RECOVERED", "PENDING")
    )

    assert recovered.id in await _claimed_ids(db)


async def test_a_reopen_before_the_tick_settles_the_unsent_pair_and_holds_the_new_open(
    pg_async_session,
):
    """1회차 열림·복구가 둘 다 안 나간 채 2회차가 열렸다 — 1회차 짝은 함께 종결, 2회차 열림은 보류."""

    db = pg_async_session
    incident = _incident(
        "MONTHLY_SLOT_GENERATION_FAILED", "RETRYING", sla=NOW + timedelta(hours=5)
    )
    incident.episode_seq = 2
    incident.first_seen_at = NOW - timedelta(minutes=10)
    db.add(incident)
    await db.flush()
    first_open = _notice(incident, episode=1, created_at=NOW - timedelta(hours=2))
    first_recovered = _notice(
        incident, "INCIDENT_RECOVERED", episode=1, created_at=NOW - timedelta(minutes=40)
    )
    second_open = _notice(incident, episode=2, created_at=NOW - timedelta(minutes=9))
    db.add_all([first_open, first_recovered, second_open])
    await db.flush()

    claimed = await _claimed_ids(db)

    assert not {first_open.id, first_recovered.id, second_open.id} & claimed
    for row in (await _row(db, first_open), await _row(db, first_recovered)):
        assert row.state == "FAILED"
        assert row.safe_error_code == RESOLVED_BEFORE_SEND_CODE
    assert (await _row(db, second_open)).state == "PENDING"


async def test_a_recovery_after_a_sent_open_still_goes_out_when_a_later_episode_reopens(
    pg_async_session,
):
    db = pg_async_session
    incident = _incident("CONTENT_GENERATION_FAILED", "RECOVERED")
    incident.episode_seq = 2
    db.add(incident)
    await db.flush()
    sent_open = _notice(incident, state="SENT", episode=1, created_at=NOW - timedelta(hours=3))
    recovered = _notice(
        incident, "INCIDENT_RECOVERED", episode=1, created_at=NOW - timedelta(hours=2)
    )
    # 뒤 에피소드의 열림이 발송 전에 종결됐어도 앞 에피소드의 나간 열림에는 복구가 따른다.
    later_open = _notice(
        incident, state="FAILED", episode=2, created_at=NOW - timedelta(minutes=30),
        safe_error_code=RESOLVED_BEFORE_SEND_CODE,
    )
    db.add_all([sent_open, recovered, later_open])
    await db.flush()

    assert recovered.id in await _claimed_ids(db)


async def test_a_recovery_is_not_sent_after_an_open_slack_never_received(pg_async_session):
    """전송 실패로 끝난 열림(FAILED)도 채널이 받지 않았다 — 그 복구 알림은 보내지 않는다."""

    db = pg_async_session
    incident = _incident("CONTENT_GENERATION_FAILED", "RECOVERED")
    db.add(incident)
    await db.flush()
    failed_open = _notice(
        incident, state="FAILED", created_at=NOW - timedelta(minutes=50),
        safe_error_code="SLACK_HTTP_ERROR",
    )
    recovered = _notice(incident, "INCIDENT_RECOVERED", created_at=NOW - timedelta(minutes=20))
    db.add_all([failed_open, recovered])
    await db.flush()

    assert recovered.id not in await _claimed_ids(db)
    assert (await _row(db, recovered)).safe_error_code == RESOLVED_BEFORE_SEND_CODE
