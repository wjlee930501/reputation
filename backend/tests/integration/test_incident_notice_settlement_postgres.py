"""발송기는 기한 안의 스윕 소유 사고와 이미 끝난 사고의 알림을 보내지 않는다(Phase 1-4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.operations import Incident, NotificationOutbox, NotificationOutboxState
from app.services.notification_delivery import _finalize
from app.services.notification_store import (
    RESOLVED_BEFORE_SEND_CODE,
    claim_notification_batch,
)
from app.services.notification_transport import TransportDecision

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


async def test_a_later_episode_recovery_is_settled_when_its_own_open_never_went_out(
    pg_async_session,
):
    """짝은 에피소드 단위다 — 1회차의 나간 열림이 2회차 복구를 내보내는 근거가 되지 않는다(Codex HOLD P2)."""

    db = pg_async_session
    incident = _incident("CONTENT_GENERATION_FAILED", "RECOVERED")
    incident.episode_seq = 2
    db.add(incident)
    await db.flush()
    e1_open = _notice(incident, state="SENT", episode=1, created_at=NOW - timedelta(hours=5))
    e1_recovered = _notice(
        incident, "INCIDENT_RECOVERED", state="SENT", episode=1, created_at=NOW - timedelta(hours=4)
    )
    e2_open = _notice(
        incident, state="FAILED", episode=2, created_at=NOW - timedelta(minutes=50),
        safe_error_code=RESOLVED_BEFORE_SEND_CODE,
    )
    e2_recovered = _notice(
        incident, "INCIDENT_RECOVERED", episode=2, created_at=NOW - timedelta(minutes=20)
    )
    db.add_all([e1_open, e1_recovered, e2_open, e2_recovered])
    await db.flush()

    assert e2_recovered.id not in await _claimed_ids(db)
    row = await _row(db, e2_recovered)
    assert row.state == "FAILED" and row.safe_error_code == RESOLVED_BEFORE_SEND_CODE


async def test_a_legacy_recovery_key_without_an_episode_keeps_the_time_ordered_rule(
    pg_async_session,
):
    db = pg_async_session
    incident = _incident("CONTENT_GENERATION_FAILED", "RECOVERED")
    db.add(incident)
    await db.flush()
    sent_open = _notice(incident, state="SENT", created_at=NOW - timedelta(hours=2))
    legacy = _notice(incident, "INCIDENT_RECOVERED", created_at=NOW - timedelta(minutes=20))
    legacy.dedupe_key = f"INCIDENT_RECOVERED:{incident.id}:v3"
    db.add_all([sent_open, legacy])
    await db.flush()

    assert legacy.id in await _claimed_ids(db)


async def test_an_episode_recovery_never_pairs_with_a_legacy_open(pg_async_session):
    """옛 키의 나간 열림이 에피소드 키 복구를 내보내는 근거가 되지 않는다(Codex R2-2).

    옛 OPEN=SENT → e2 OPEN=발송 전 종결 → e2 RECOVERED. e2의 짝은 e2 열림뿐이다.
    """

    db = pg_async_session
    incident = _incident("CONTENT_GENERATION_FAILED", "RECOVERED")
    incident.episode_seq = 2
    db.add(incident)
    await db.flush()
    legacy_open = _notice(incident, state="SENT", created_at=NOW - timedelta(hours=5))
    legacy_open.dedupe_key = f"INCIDENT_OPEN:{incident.id}:v1"
    e2_open = _notice(
        incident, state="FAILED", episode=2, created_at=NOW - timedelta(minutes=50),
        safe_error_code=RESOLVED_BEFORE_SEND_CODE,
    )
    e2_recovered = _notice(
        incident, "INCIDENT_RECOVERED", episode=2, created_at=NOW - timedelta(minutes=20)
    )
    db.add_all([legacy_open, e2_open, e2_recovered])
    await db.flush()

    assert e2_recovered.id not in await _claimed_ids(db)
    row = await _row(db, e2_recovered)
    assert row.state == "FAILED" and row.safe_error_code == RESOLVED_BEFORE_SEND_CODE


async def test_both_legacy_keys_keep_the_time_ordered_pairing(pg_async_session):
    db = pg_async_session
    reached = _incident("CONTENT_GENERATION_FAILED", "RECOVERED")
    unreached = _incident("CONTENT_GENERATION_FAILED", "RECOVERED")
    db.add_all([reached, unreached])
    await db.flush()
    rows = []
    for incident, open_state in ((reached, "SENT"), (unreached, "FAILED")):
        legacy_open = _notice(
            incident, state=open_state, created_at=NOW - timedelta(hours=2),
            safe_error_code="SLACK_HTTP_ERROR" if open_state == "FAILED" else None,
        )
        legacy_open.dedupe_key = f"INCIDENT_OPEN:{incident.id}:v1"
        legacy_recovered = _notice(
            incident, "INCIDENT_RECOVERED", created_at=NOW - timedelta(minutes=20)
        )
        legacy_recovered.dedupe_key = f"INCIDENT_RECOVERED:{incident.id}:v3"
        rows.append(legacy_recovered)
        db.add_all([legacy_open, legacy_recovered])
    await db.flush()

    claimed = await _claimed_ids(db)

    assert rows[0].id in claimed
    assert rows[1].id not in claimed
    assert (await _row(db, rows[1])).safe_error_code == RESOLVED_BEFORE_SEND_CODE


# 종결 상한(200)을 넘는 후보 — 상한 밖의 하나가 claim으로 새지 않는지 본다(Codex R3-1).
_OVER_SETTLE_LIMIT = 201


async def _sendable_bystander(db) -> NotificationOutbox:
    """종결 대상이 아닌, 정말 나가야 하는 열림 알림(열린 사고)."""

    live = _incident("CONTENT_GENERATION_FAILED", "OPEN")
    db.add(live)
    await db.flush()
    notice = _notice(live, created_at=NOW - timedelta(minutes=1))
    db.add(notice)
    await db.flush()
    return notice


async def test_recoveries_beyond_the_settle_limit_are_never_claimed(pg_async_session):
    """열림이 모두 FAILED인 복구가 종결 상한보다 많아도 상한 밖의 복구는 나가지 않는다."""

    db = pg_async_session
    incidents = [
        _incident("CONTENT_GENERATION_FAILED", "RECOVERED") for _ in range(_OVER_SETTLE_LIMIT)
    ]
    db.add_all(incidents)
    await db.flush()
    recoveries = []
    for incident in incidents:
        failed_open = _notice(
            incident, state="FAILED", created_at=NOW - timedelta(minutes=50),
            safe_error_code="SLACK_HTTP_ERROR",
        )
        recovered = _notice(incident, "INCIDENT_RECOVERED", created_at=NOW - timedelta(minutes=20))
        recoveries.append(recovered)
        db.add_all([failed_open, recovered])
    await db.flush()
    bystander = await _sendable_bystander(db)

    claimed = await _claimed_ids(db)

    assert not claimed & {row.id for row in recoveries}
    assert bystander.id in claimed


async def test_unsent_opens_of_resolved_incidents_beyond_the_settle_limit_are_never_claimed(
    pg_async_session,
):
    db = pg_async_session
    incidents = [
        _incident("CONTENT_GENERATION_FAILED", "RECOVERED") for _ in range(_OVER_SETTLE_LIMIT)
    ]
    db.add_all(incidents)
    await db.flush()
    opens = [_notice(incident) for incident in incidents]
    db.add_all(opens)
    await db.flush()
    bystander = await _sendable_bystander(db)

    claimed = await _claimed_ids(db)

    assert not claimed & {row.id for row in opens}
    assert bystander.id in claimed


async def test_a_recovery_never_leaves_before_its_pending_open_beyond_the_settle_limit(
    pg_async_session,
):
    """열림·복구가 둘 다 대기 중인 짝이 종결 상한보다 많아도 복구만 혼자 나가지 않는다(Codex R4-1).

    종결 배치(200)가 닿지 못한 201번째 열림은 PENDING으로 남는다. 그 복구는 '나갔을 수 있는 열림'이
    있어 종결되지 않지만, 열림이 아직 나가지 않았으므로 집히지도 않는다 — 다음 tick이 둘을 함께 종결한다.
    """

    db = pg_async_session
    incidents = [
        _incident("CONTENT_GENERATION_FAILED", "RECOVERED") for _ in range(_OVER_SETTLE_LIMIT)
    ]
    db.add_all(incidents)
    await db.flush()
    pairs = []
    for incident in incidents:
        opened = _notice(incident, created_at=NOW - timedelta(minutes=50))
        recovered = _notice(incident, "INCIDENT_RECOVERED", created_at=NOW - timedelta(minutes=20))
        pairs.append((opened, recovered))
        db.add_all([opened, recovered])
    await db.flush()
    bystander = await _sendable_bystander(db)
    every_notice = {row.id for pair in pairs for row in pair}

    first_tick = await _claimed_ids(db)

    assert not first_tick & every_notice
    assert bystander.id in first_tick

    second_tick = await _claimed_ids(db)

    assert not second_tick & every_notice
    for pair in pairs:
        for row in pair:
            settled = await _row(db, row)
            assert settled.state == "FAILED"
            assert settled.safe_error_code == RESOLVED_BEFORE_SEND_CODE


async def test_a_recovery_waits_for_its_open_and_follows_it_on_the_next_tick(pg_async_session):
    """복구 알림은 같은 에피소드의 열림 알림이 실제로 나간 뒤에만 나간다."""

    db = pg_async_session
    # 열림이 아직 나가지 않았는데 사고가 종결 대상이 아닌 경우(예: 종결 배치가 닿지 않은 상태)를
    # 사고 상태로 재현한다 — 열림은 보낼 차례이고 복구는 그 뒤를 기다린다.
    incident = _incident("CONTENT_GENERATION_FAILED", "OPEN")
    db.add(incident)
    await db.flush()
    opened = _notice(incident, created_at=NOW - timedelta(minutes=50))
    recovered = _notice(incident, "INCIDENT_RECOVERED", created_at=NOW - timedelta(minutes=20))
    db.add_all([opened, recovered])
    await db.flush()

    first_tick = await claim_notification_batch(db, "worker-a", now=NOW)
    [open_lease] = [claim for claim in first_tick if claim.id == opened.id]

    assert recovered.id not in {claim.id for claim in first_tick}
    assert (await _row(db, recovered)).state == "PENDING"

    # 열림은 SENDING으로 커밋됐지만 아직 HTTP를 보내지 않았다 — 복구는 이번에도 기다린다(Codex R5-1).
    assert recovered.id not in await _claimed_ids(db)
    assert (await _row(db, recovered)).state == "PENDING"

    # 열림 전송이 일시 실패했다. 다음 시도에서도 열림만 나가고 복구는 기다린다.
    await _finalize(db, open_lease, _decision("RETRYING", "SLACK_SERVER_ERROR"), NOW)
    retry_at = NOW + timedelta(hours=1)
    retry_tick = await claim_notification_batch(db, "worker-b", now=retry_at)
    [retry_lease] = [claim for claim in retry_tick if claim.id == opened.id]
    assert recovered.id not in {claim.id for claim in retry_tick}

    # 재시도가 Slack에 닿은 뒤에야 복구가 나간다.
    await _finalize(db, retry_lease, _decision("SENT", None), retry_at)
    after_sent = {
        claim.id for claim in await claim_notification_batch(db, "worker-c", now=retry_at)
    }
    assert recovered.id in after_sent


def _decision(state: str, code: str | None) -> TransportDecision:
    return TransportDecision(NotificationOutboxState(state), code, {"status": 200}, attempted=True)


async def test_a_recovery_is_settled_when_its_claimed_open_never_reached_slack(pg_async_session):
    """워커 A가 열림을 잡은(SENDING) 사이 사고가 복구됐고, A의 전송이 실패했다(Codex R5-1).

    복구 알림을 다른 워커가 먼저 내보내면 채널에는 열림 없는 복구 줄만 남는다. 복구는 열림이
    결판날 때까지 기다리고, 열림이 끝내 나가지 못하면 둘 다 발송 전 종결된다.
    """

    db = pg_async_session
    incident = _incident("CONTENT_GENERATION_FAILED", "OPEN")
    db.add(incident)
    await db.flush()
    opened = _notice(incident, created_at=NOW - timedelta(minutes=50))
    db.add(opened)
    await db.flush()
    [open_lease] = [
        claim
        for claim in await claim_notification_batch(db, "worker-a", now=NOW)
        if claim.id == opened.id
    ]

    incident.state = "RECOVERED"
    incident.recovered_at = NOW
    recovered = _notice(incident, "INCIDENT_RECOVERED", created_at=NOW)
    recovered.next_attempt_at = NOW
    db.add(recovered)
    await db.flush()

    assert recovered.id not in await _claimed_ids(db)
    assert (await _row(db, recovered)).state == "PENDING"

    await _finalize(db, open_lease, _decision("RETRYING", "SLACK_SERVER_ERROR"), NOW)
    later = {claim.id for claim in await claim_notification_batch(db, "worker-b", now=NOW + timedelta(hours=1))}

    assert opened.id not in later and recovered.id not in later
    for row in (opened, recovered):
        settled = await _row(db, row)
        assert settled.state == "FAILED"
        assert settled.safe_error_code == RESOLVED_BEFORE_SEND_CODE


async def test_a_recovery_follows_a_claimed_open_once_its_send_lands(pg_async_session):
    """사고가 복구된 사이 잡혀 있던 열림이 결국 Slack에 닿았다 — 그 뒤 복구가 나간다."""

    db = pg_async_session
    incident = _incident("CONTENT_GENERATION_FAILED", "OPEN")
    db.add(incident)
    await db.flush()
    opened = _notice(incident, created_at=NOW - timedelta(minutes=50))
    db.add(opened)
    await db.flush()
    [open_lease] = [
        claim
        for claim in await claim_notification_batch(db, "worker-a", now=NOW)
        if claim.id == opened.id
    ]
    incident.state = "RECOVERED"
    incident.recovered_at = NOW
    recovered = _notice(incident, "INCIDENT_RECOVERED", created_at=NOW)
    recovered.next_attempt_at = NOW
    db.add(recovered)
    await db.flush()

    assert recovered.id not in await _claimed_ids(db)

    await _finalize(db, open_lease, _decision("SENT", None), NOW)

    assert recovered.id in await _claimed_ids(db)


@pytest.mark.parametrize(
    ("open_state", "open_code", "recovery_claimed", "recovery_state"),
    [
        ("SENT", None, True, "SENDING"),
        # 잡혔지만 아직 보내지 않았을 수 있는 열림 — 결판(SENT·실패)이 날 때까지 복구는 기다린다.
        ("SENDING", None, False, "PENDING"),
        # 보낸 뒤 결과를 모르는 열림 — 채널에 닿았을 수 있으므로 복구가 따른다.
        ("HOLD", "DELIVERY_OUTCOME_UNKNOWN", True, "SENDING"),
        # 보낼 채널이 없어 보내기 전에 보류한 열림 — 아직 나가지 않았으므로 복구는 기다린다.
        ("HOLD", "SLACK_CHANNEL_UNAVAILABLE", False, "PENDING"),
        ("RETRYING", None, False, "PENDING"),
        ("FAILED", "SLACK_PERMANENT_ERROR", False, "FAILED"),
    ],
)
async def test_a_recovery_is_claimed_only_after_its_open_went_out(
    pg_async_session, open_state, open_code, recovery_claimed, recovery_state
):
    db = pg_async_session
    incident = _incident("CONTENT_GENERATION_FAILED", "OPEN")
    db.add(incident)
    await db.flush()
    opened = _notice(
        incident, state=open_state, created_at=NOW - timedelta(minutes=50),
        safe_error_code=open_code,
    )
    if open_state == "RETRYING":
        # 일시 실패한 열림의 다음 시도는 아직 오지 않았다.
        opened.next_attempt_at = NOW + timedelta(minutes=5)
    if open_state == "SENDING":
        opened.lease_owner = "other-worker"
        opened.lease_expires_at = NOW + timedelta(minutes=1)
    recovered = _notice(incident, "INCIDENT_RECOVERED", created_at=NOW - timedelta(minutes=20))
    db.add_all([opened, recovered])
    await db.flush()

    claimed = await _claimed_ids(db)

    assert (recovered.id in claimed) is recovery_claimed
    assert (await _row(db, recovered)).state == recovery_state


async def test_an_episode_recovery_without_any_open_of_its_episode_is_settled(pg_async_session):
    """2회차 열림이 outbox에 없는데(알리지 않고 다시 열림) 2회차 복구만 있다 — 짝 없는 복구는 보내지 않는다."""

    db = pg_async_session
    incident = _incident("CONTENT_GENERATION_FAILED", "RECOVERED")
    incident.episode_seq = 2
    db.add(incident)
    await db.flush()
    e1_open = _notice(incident, state="SENT", episode=1, created_at=NOW - timedelta(hours=5))
    e1_recovered = _notice(
        incident, "INCIDENT_RECOVERED", state="SENT", episode=1, created_at=NOW - timedelta(hours=4)
    )
    e2_recovered = _notice(
        incident, "INCIDENT_RECOVERED", episode=2, created_at=NOW - timedelta(minutes=20)
    )
    db.add_all([e1_open, e1_recovered, e2_recovered])
    await db.flush()

    assert e2_recovered.id not in await _claimed_ids(db)
    row = await _row(db, e2_recovered)
    assert row.state == "FAILED" and row.safe_error_code == RESOLVED_BEFORE_SEND_CODE
