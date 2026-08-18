"""Durable Slack outbox behavior and PostgreSQL concurrency proofs."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import anyio
import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.audit import AdminAuditLog
from app.models.operations import (
    Incident,
    IncidentState,
    NotificationOutbox,
    NotificationOutboxState,
)
from app.services.notification_outbox import (
    DispatchResult,
    IncidentSlackProjection,
    NotificationIntent,
    NotificationPayloadError,
    NotificationRetryConflict,
    SlackMessage,
    build_open_incident_notification,
    build_recovered_incident_notification,
    build_summary_notification,
    claim_notification_batch,
    dispatch_notification_batch,
    enqueue_notification,
    recover_stale_sending,
    retry_notification,
)
from app.services.notification_success_hooks import reconcile_sent_notification_incidents
from app.workers import notification_tasks

_DATABASE_URL = "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"
_NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


def _intent(key: str, *, max_attempts: int = 3) -> NotificationIntent:
    message = SlackMessage(
        fallback_text="운영 알림",
        blocks=(
            {"type": "section", "block_id": "summary", "text": {"type": "mrkdwn", "text": "확인 필요"}},
            {
                "type": "actions",
                "block_id": "action",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Admin에서 확인"},
                        "url": "http://localhost:3000/operations",
                    }
                ],
            },
        ),
        admin_url="http://localhost:3000/operations",
    )
    return NotificationIntent(
        dedupe_key=key,
        notification_type="INCIDENT_OPEN",
        message=message,
        max_attempts=max_attempts,
    )


def _projection(incident_id: uuid.UUID | None = None) -> IncidentSlackProjection:
    return IncidentSlackProjection(
        incident_id=incident_id or uuid.UUID("a1000000-0000-0000-0000-000000000001"),
        hospital_name="장편한외과의원",
        severity="HIGH",
        customer_impact="콘텐츠 발행이 지연되고 있습니다.",
        next_action="재시도를 확인해 주세요.",
        admin_path="/operations?state=OPEN",
        owner_label="김효진 팀장",
        sla_label="오늘 18:00",
    )


def _urls(value: object) -> list[str]:
    if isinstance(value, dict):
        return [item for key, nested in value.items() for item in ([nested] if key == "url" else _urls(nested)) if isinstance(item, str)]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _urls(nested)]
    return []


@pytest.fixture
async def outbox_sessions():
    engine = create_async_engine(_DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as cleanup:
        await cleanup.execute(text("DELETE FROM incidents WHERE source_type='NOTIFICATION_OUTBOX' AND source_id IN (SELECT id::text FROM notification_outbox WHERE dedupe_key LIKE 'OPS-QA-T10-%')"))
        await cleanup.execute(text("DELETE FROM notification_outbox WHERE dedupe_key LIKE 'OPS-QA-T10-%'"))
        await cleanup.execute(text("DELETE FROM incidents WHERE dedupe_key LIKE 'OPS-QA-T10-%'"))
        await cleanup.commit()
    try:
        yield sessions
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(text("DELETE FROM incidents WHERE source_type='NOTIFICATION_OUTBOX' AND source_id IN (SELECT id::text FROM notification_outbox WHERE dedupe_key LIKE 'OPS-QA-T10-%')"))
            await cleanup.execute(text("DELETE FROM notification_outbox WHERE dedupe_key LIKE 'OPS-QA-T10-%'"))
            await cleanup.execute(text("DELETE FROM incidents WHERE dedupe_key LIKE 'OPS-QA-T10-%'"))
            await cleanup.commit()
        await engine.dispose()


def test_payload_builders_have_one_safe_admin_link_and_deterministic_summary() -> None:
    # Given: the same incidents in different input orders
    first = _projection()
    second = _projection(uuid.UUID("a1000000-0000-0000-0000-000000000002"))
    start = datetime(2026, 8, 10, tzinfo=UTC)
    end = start + timedelta(hours=1)

    # When: OPEN, RECOVERED and SUMMARY projections are built
    open_intent = build_open_incident_notification(first, "https://admin.example.test")
    recovered = build_recovered_incident_notification(first, "https://admin.example.test")
    summary_a = build_summary_notification((second, first), start, end, "CONTENT_FAILURE", "https://admin.example.test")
    summary_b = build_summary_notification((first, second), start, end, "CONTENT_FAILURE", "https://admin.example.test")

    # Then: every payload is valid and summary identity ignores input ordering only
    for intent in (open_intent, recovered, summary_a):
        assert intent.message.fallback_text.strip()
        assert len(intent.message.blocks) <= 50
        block_ids = [str(block["block_id"]) for block in intent.message.blocks]
        assert len(block_ids) == len(set(block_ids))
    assert _urls(open_intent.message.blocks) == [
        "https://admin.example.test/operations?state=OPEN"
    ]
    assert _urls(recovered.message.blocks) == [
        "https://admin.example.test/operations?state=OPEN"
    ]
    assert _urls(summary_a.message.blocks) == [
        "https://admin.example.test/operations?queue=incidents&status=OPEN"
    ]
    assert summary_a.dedupe_key == summary_b.dedupe_key
    assert str(first.incident_id) not in summary_a.message.payload_json()
    assert str(first.operation_run_id) not in summary_a.message.payload_json()
    assert str(second.incident_id) not in summary_a.message.payload_json()
    assert all(
        label in summary_a.message.payload_json()
        for label in ("무슨 문제인지", "고객 영향", "지금 할 일")
    )
    assert "개발팀에 전달할 정보" in summary_a.message.payload_json()
    assert open_intent.notification_type == "INCIDENT_OPEN"
    assert recovered.notification_type == "INCIDENT_RECOVERED"
    assert open_intent.dedupe_key.endswith(":e1")
    assert recovered.dedupe_key.endswith(":e1")
    assert build_open_incident_notification(
        replace(first, version=99), "https://admin.example.test"
    ).dedupe_key == open_intent.dedupe_key
    assert build_open_incident_notification(
        replace(first, episode_seq=2), "https://admin.example.test"
    ).dedupe_key.endswith(":e2")
    assert "처리 기한: 오늘 18:00" in open_intent.message.payload_json()
    assert "SLA:" not in open_intent.message.payload_json()
    assert "운영센터에서 조치하기" in open_intent.message.payload_json()
    recovered_payload = recovered.message.payload_json()
    assert "자동 복구가 확인되었습니다" in recovered_payload
    assert "운영센터에서 복구 결과를 확인" in recovered_payload
    assert "복구 상태 확인" in recovered_payload
    assert "재시도를 확인해 주세요" not in recovered_payload


def test_summary_identity_uses_sorted_unique_incidents_and_rejects_conflicts() -> None:
    # Given: an exact duplicate and a conflicting projection for one incident ID
    first = _projection()
    second = _projection(uuid.UUID("a1000000-0000-0000-0000-000000000002"))
    start = datetime(2026, 8, 10, tzinfo=UTC)
    end = start + timedelta(hours=1)

    # When: exact duplicates are summarized
    deduped = build_summary_notification(
        (first, first, second), start, end, "CONTENT_FAILURE", "https://admin.example.test"
    )
    canonical = build_summary_notification(
        (second, first), start, end, "CONTENT_FAILURE", "https://admin.example.test"
    )

    # Then: identity/count/body use the unique set, while conflicting facts fail closed
    assert deduped.dedupe_key == canonical.dedupe_key
    assert all(
        label in deduped.message.fallback_text
        for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:", "처리 기한:")
    )
    assert str(first.incident_id) not in deduped.message.payload_json()
    with pytest.raises(NotificationPayloadError, match="SUMMARY_INCIDENT_CONFLICT"):
        build_summary_notification(
            (first, replace(first, hospital_name="다른 병원")),
            start,
            end,
            "CONTENT_FAILURE",
            "https://admin.example.test",
        )


@pytest.mark.asyncio
async def test_enqueue_is_caller_transactional_and_deduplicated(outbox_sessions) -> None:
    # Given: one deterministic notification intent
    key = "OPS-QA-T10-DEDUPE"

    # When: it is enqueued twice in one caller transaction
    async with outbox_sessions() as db:
        first = await enqueue_notification(db, _intent(key), now=_NOW)
        second = await enqueue_notification(db, _intent(key), now=_NOW)
        assert first.id == second.id
        await db.rollback()

    # Then: rollback removes the intent because enqueue never commits for the caller
    async with outbox_sessions() as verify:
        count = await verify.scalar(select(func.count(NotificationOutbox.id)).where(NotificationOutbox.dedupe_key == key))
        assert count == 0


@pytest.mark.asyncio
async def test_enqueue_rejects_non_admin_link(outbox_sessions) -> None:
    # Given: structurally valid Block Kit pointing away from the configured Admin
    unsafe = _intent("OPS-QA-T10-UNSAFE-LINK")
    unsafe_message = replace(
        unsafe.message,
        admin_url="https://evil.example.test/operations",
        blocks=(
            unsafe.message.blocks[0],
            replace_url(unsafe.message.blocks[1], "https://evil.example.test/operations"),
        ),
    )

    # When/Then: the caller transaction refuses the exfiltration link
    async with outbox_sessions() as db:
        with pytest.raises(NotificationPayloadError, match="SLACK_ADMIN_LINK_INVALID"):
            await enqueue_notification(db, replace(unsafe, message=unsafe_message), now=_NOW)


def replace_url(block: dict, url: str) -> dict:
    copied = {**block}
    copied["elements"] = [{**block["elements"][0], "url": url}]
    return copied


@pytest.mark.asyncio
async def test_two_workers_never_claim_the_same_row(outbox_sessions) -> None:
    # Given: two due outbox rows
    async with outbox_sessions() as db:
        await enqueue_notification(db, _intent("OPS-QA-T10-CLAIM-A"), now=_NOW)
        await enqueue_notification(db, _intent("OPS-QA-T10-CLAIM-B"), now=_NOW)
        await db.commit()
    claims: list[tuple[str, tuple[uuid.UUID, ...]]] = []

    async def claim(worker: str) -> None:
        async with outbox_sessions() as db:
            rows = await claim_notification_batch(db, worker, now=_NOW, limit=2)
            claims.append((worker, tuple(row.id for row in rows)))

    # When: independent PostgreSQL sessions claim concurrently
    async with anyio.create_task_group() as group:
        group.start_soon(claim, "worker-a")
        group.start_soon(claim, "worker-b")

    # Then: each row is leased once, with no overlapping IDs
    claimed_ids = [row_id for _, ids in claims for row_id in ids]
    assert len(claimed_ids) == 2
    assert len(set(claimed_ids)) == 2


@pytest.mark.asyncio
async def test_stale_sending_lease_moves_to_hold(outbox_sessions) -> None:
    # Given: a SENDING row whose delivery process disappeared after I/O may have started
    async with outbox_sessions() as db:
        row = NotificationOutbox(
            dedupe_key="OPS-QA-T10-STALE",
            notification_type="INCIDENT_OPEN",
            channel="SLACK",
            state=NotificationOutboxState.SENDING,
            payload=_intent("ignored").message.payload(),
            fallback_text="운영 알림",
            attempt_count=1,
            max_attempts=3,
            next_attempt_at=None,
            lease_owner="dead-worker",
            lease_expires_at=_NOW - timedelta(seconds=1),
        )
        db.add(row)
        await db.commit()

    # When: stale leases are reconciled
    async with outbox_sessions() as db:
        recovered = await recover_stale_sending(db, now=_NOW)

    # Then: the ambiguous outcome is held, never blindly retried
    assert recovered == 1
    async with outbox_sessions() as verify:
        row = await verify.scalar(select(NotificationOutbox).where(NotificationOutbox.dedupe_key == "OPS-QA-T10-STALE"))
        assert row is not None
        assert row.state == NotificationOutboxState.HOLD
        assert row.safe_error_code == "DELIVERY_OUTCOME_UNKNOWN"
        assert row.next_attempt_at is None
        assert row.lease_owner is None
        assert row.incident_id is not None
        incident = await verify.get(Incident, row.incident_id)
        assert incident is not None
        assert incident.incident_type == "NOTIFICATION_DELIVERY_UNKNOWN"
        assert incident.source_id == str(row.id)
        assert incident.safe_error_code == "DELIVERY_OUTCOME_UNKNOWN"


async def _dispatch_once(outbox_sessions, key: str, handler, *, now: datetime = _NOW, max_attempts: int = 3):
    async with outbox_sessions() as setup:
        if await setup.scalar(select(func.count(NotificationOutbox.id)).where(NotificationOutbox.dedupe_key == key)) == 0:
            await enqueue_notification(setup, _intent(key, max_attempts=max_attempts), now=now)
            await setup.commit()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await dispatch_notification_batch(
            outbox_sessions,
            client,
            webhook_url="https://hooks.slack.com/services/T/B/X",
            worker_id=f"worker-{uuid.uuid4().hex}",
            now=now,
            limit=10,
        )


@pytest.mark.asyncio
async def test_http_500_retries_then_exact_ok_marks_sent(outbox_sessions) -> None:
    # Given: Slack fails once and then accepts the exact same durable intent
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, text="secret upstream detail") if attempts == 1 else httpx.Response(200, text="ok")

    # When: two dispatcher ticks run after the retry becomes due
    first = await _dispatch_once(outbox_sessions, "OPS-QA-T10-RETRY", handler)
    second = await _dispatch_once(outbox_sessions, "OPS-QA-T10-RETRY", handler, now=_NOW + timedelta(minutes=1))

    # Then: one HTTP attempt per tick, RETRYING becomes SENT, and raw body is absent
    assert (first.retried, first.sent) == (1, 0)
    assert (second.retried, second.sent) == (0, 1)
    assert attempts == 2
    async with outbox_sessions() as verify:
        row = await verify.scalar(select(NotificationOutbox).where(NotificationOutbox.dedupe_key == "OPS-QA-T10-RETRY"))
        assert row is not None and row.state == NotificationOutboxState.SENT
        assert row.provider_message_id is None
        assert "secret" not in str(row.provider_response)


@pytest.mark.asyncio
async def test_slack_success_never_recovers_the_linked_incident(outbox_sessions) -> None:
    # Given: an OPEN source-of-truth incident linked to a pending Slack projection
    async with outbox_sessions() as db:
        incident = Incident(
            dedupe_key="OPS-QA-T10-INCIDENT-SOURCE",
            incident_type="CONTENT_FAILURE",
            state=IncidentState.OPEN,
            severity="HIGH",
            customer_impact="발행 지연",
            source_type="CONTENT",
            next_action="운영 센터 확인",
            admin_path="/operations",
        )
        db.add(incident)
        await db.flush()
        await enqueue_notification(
            db,
            replace(
                _intent("OPS-QA-T10-INCIDENT-NOTIFY"),
                incident_id=incident.id,
            ),
            now=_NOW,
        )
        await db.commit()
        incident_id = incident.id

    # When: Slack acknowledges the notification
    result = await _dispatch_once(
        outbox_sessions,
        "OPS-QA-T10-INCIDENT-NOTIFY",
        lambda _request: httpx.Response(200, text="ok"),
    )

    # Then: only delivery is SENT; incident lifecycle remains OPEN and unchanged
    assert result.sent == 1
    async with outbox_sessions() as verify:
        incident = await verify.get(Incident, incident_id)
        assert incident is not None
        assert incident.state == IncidentState.OPEN
        assert incident.version == 1
        assert incident.recovered_at is None


@pytest.mark.asyncio
async def test_retry_success_recovers_notification_delivery_incident(outbox_sessions) -> None:
    # Given: the delivery incident for an outbox row is open after a retryable failure
    async with outbox_sessions() as db:
        row = await enqueue_notification(db, _intent("OPS-QA-T10-DELIVERY-RECOVERY"), now=_NOW)
        row.state = NotificationOutboxState.RETRYING
        row.attempt_count = 1
        row.next_attempt_at = _NOW
        await db.flush()
        incident = Incident(
            dedupe_key="OPS-QA-T10-DELIVERY-RECOVERY-INCIDENT",
            incident_type="NOTIFICATION_DELIVERY_FAILED",
            state=IncidentState.OPEN,
            severity="HIGH",
            customer_impact="운영 알림 미전달",
            source_type="NOTIFICATION_OUTBOX",
            source_id=str(row.id),
            safe_error_code="DELIVERY_RETRY_EXHAUSTED",
            next_action="Slack 설정 확인 후 재시도",
            admin_path="/operations",
        )
        db.add(incident)
        await db.flush()
        row.incident_id = incident.id
        row_id = row.id
        incident_id = incident.id
        await db.commit()

    # When: the retry is accepted by Slack
    result = await _dispatch_once(
        outbox_sessions,
        "OPS-QA-T10-DELIVERY-RECOVERY",
        lambda _request: httpx.Response(200, text="ok"),
    )

    # Then: only the delivery incident is recovered by the observed send
    assert result.sent == 1
    async with outbox_sessions() as verify:
        row = await verify.get(NotificationOutbox, row_id)
        incident = await verify.get(Incident, incident_id)
        assert row is not None and row.state == NotificationOutboxState.SENT
        assert incident is not None
        assert incident.state == IncidentState.RECOVERED
        assert incident.recovered_at == _NOW


@pytest.mark.asyncio
async def test_rate_limit_honors_retry_after_and_permanent_4xx_fails(outbox_sessions) -> None:
    # Given: one rate limit and one permanent invalid payload
    rate = await _dispatch_once(
        outbox_sessions,
        "OPS-QA-T10-429",
        lambda _request: httpx.Response(429, text="ratelimited", headers={"Retry-After": "7200"}),
    )
    failed = await _dispatch_once(
        outbox_sessions,
        "OPS-QA-T10-400",
        lambda _request: httpx.Response(400, text="invalid_payload"),
    )

    # When/Then: Retry-After is exact while permanent 4xx has no next attempt
    assert (rate.retried, failed.failed) == (1, 1)
    async with outbox_sessions() as verify:
        rows = {row.dedupe_key: row for row in await verify.scalars(select(NotificationOutbox).where(NotificationOutbox.dedupe_key.in_(("OPS-QA-T10-429", "OPS-QA-T10-400"))))}
        assert rows["OPS-QA-T10-429"].next_attempt_at == _NOW + timedelta(seconds=7200)
        assert rows["OPS-QA-T10-400"].state == NotificationOutboxState.FAILED
        assert rows["OPS-QA-T10-400"].next_attempt_at is None
        assert rows["OPS-QA-T10-400"].provider_response == {"http_status": 400, "body_code": "invalid_payload"}
        delivery_incident = await verify.scalar(
            select(Incident).where(
                Incident.source_type == "NOTIFICATION_OUTBOX",
                Incident.source_id == str(rows["OPS-QA-T10-400"].id),
            )
        )
        assert delivery_incident is not None
        assert rows["OPS-QA-T10-400"].incident_id == delivery_incident.id
        assert delivery_incident.state == IncidentState.OPEN
        assert delivery_incident.safe_error_code == "SLACK_PERMANENT_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["read_timeout", "non_ok_200", "remote_protocol"])
async def test_ambiguous_delivery_outcomes_are_held(outbox_sessions, outcome: str) -> None:
    # Given: an outcome where Slack may have accepted the request
    def handler(request: httpx.Request) -> httpx.Response:
        if outcome == "read_timeout":
            raise httpx.ReadTimeout("ambiguous", request=request)
        if outcome == "remote_protocol":
            raise httpx.RemoteProtocolError("ambiguous")
        return httpx.Response(200, text="not-ok")

    # When: one dispatcher tick observes it
    result = await _dispatch_once(outbox_sessions, f"OPS-QA-T10-HOLD-{outcome}", handler)

    # Then: it requires operator reconciliation instead of replay
    assert result.held == 1
    async with outbox_sessions() as verify:
        row = await verify.scalar(select(NotificationOutbox).where(NotificationOutbox.dedupe_key == f"OPS-QA-T10-HOLD-{outcome}"))
        assert row is not None and row.state == NotificationOutboxState.HOLD
        assert row.safe_error_code == "DELIVERY_OUTCOME_UNKNOWN"
        assert row.next_attempt_at is None
        assert row.incident_id is not None
        incident = await verify.get(Incident, row.incident_id)
        assert incident is not None
        assert incident.incident_type == "NOTIFICATION_DELIVERY_UNKNOWN"
        assert incident.source_id == str(row.id)
        assert incident.safe_error_code == "DELIVERY_OUTCOME_UNKNOWN"


@pytest.mark.asyncio
async def test_transient_failure_stops_at_max_attempts(outbox_sessions) -> None:
    # Given: a row already at its final allowed HTTP attempt
    # When: Slack returns a retryable server error
    result = await _dispatch_once(
        outbox_sessions,
        "OPS-QA-T10-MAX",
        lambda _request: httpx.Response(503, text="down"),
        max_attempts=1,
    )

    # Then: the row terminates FAILED with no retry schedule
    assert result.failed == 1
    async with outbox_sessions() as verify:
        row = await verify.scalar(select(NotificationOutbox).where(NotificationOutbox.dedupe_key == "OPS-QA-T10-MAX"))
        assert row is not None and row.state == NotificationOutboxState.FAILED
        assert row.safe_error_code == "DELIVERY_RETRY_EXHAUSTED"
        assert row.next_attempt_at is None


@pytest.mark.asyncio
async def test_multi_row_dispatch_throttles_between_actual_posts_only(outbox_sessions) -> None:
    # Given: two due rows and an injected no-sleep throttle probe
    async with outbox_sessions() as db:
        await enqueue_notification(db, _intent("OPS-QA-T10-THROTTLE-A"), now=_NOW)
        await enqueue_notification(db, _intent("OPS-QA-T10-THROTTLE-B"), now=_NOW)
        await db.commit()
    posts = 0
    pauses = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal posts
        posts += 1
        return httpx.Response(200, text="ok")

    async def throttle() -> None:
        nonlocal pauses
        pauses += 1

    # When: one batch sends both rows
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await dispatch_notification_batch(
            outbox_sessions,
            client,
            webhook_url="https://hooks.slack.com/services/T/B/X",
            worker_id="worker-throttle",
            now=_NOW,
            limit=2,
            throttle=throttle,
        )

    # Then: two one-shot POSTs have exactly one inter-send pause and none after the last
    assert result.sent == 2
    assert posts == 2
    assert pauses == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_state",
    (NotificationOutboxState.HOLD, NotificationOutboxState.FAILED),
)
async def test_manual_retry_is_audited_cas_and_replay_safe(
    outbox_sessions, initial_state: NotificationOutboxState
) -> None:
    # Given: an operator-retryable delivery with a known optimistic version
    async with outbox_sessions() as db:
        row = NotificationOutbox(
            dedupe_key=f"OPS-QA-T10-MANUAL-{initial_state.value}",
            notification_type="INCIDENT_OPEN",
            channel="SLACK",
            state=initial_state,
            payload=_intent("ignored").message.payload(),
            fallback_text="운영 알림",
            attempt_count=1,
            max_attempts=3,
            next_attempt_at=None,
            safe_error_code="DELIVERY_OUTCOME_UNKNOWN",
            provider_response={"http_status": 200, "body_code": "non_ok"},
            version=4,
        )
        db.add(row)
        await db.commit()
        row_id = row.id

    # When: the same expected-version command is replayed
    async with outbox_sessions() as db:
        first = await retry_notification(
            db,
            row_id,
            expected_version=4,
            actor="ops@example.test",
            reason="Slack에서 미수신 확인",
            now=_NOW,
        )
        await db.commit()
    async with outbox_sessions() as db:
        replay = await retry_notification(
            db,
            row_id,
            expected_version=4,
            actor="ops@example.test",
            reason="Slack에서 미수신 확인",
            now=_NOW,
        )
        await db.commit()

    # Then: one RETRYING transition and one audit row exist
    assert not isinstance(first, NotificationRetryConflict)
    assert not isinstance(replay, NotificationRetryConflict)
    assert first.id == replay.id and replay.version == 5
    async with outbox_sessions() as verify:
        refreshed = await verify.get(NotificationOutbox, row_id)
        audit_count = await verify.scalar(
            select(func.count(AdminAuditLog.id)).where(
                AdminAuditLog.action == "notification_retry_requested",
                AdminAuditLog.target_id == str(row_id),
            )
        )
        assert refreshed is not None
        assert refreshed.state == NotificationOutboxState.RETRYING
        assert refreshed.attempt_count == 0
        assert refreshed.next_attempt_at == _NOW
        assert refreshed.safe_error_code is None
        assert refreshed.provider_response is None
        assert audit_count == 1


@pytest.mark.asyncio
async def test_manual_retry_rejects_illegal_or_stale_state(outbox_sessions) -> None:
    # Given: a terminal success that must never be resent
    async with outbox_sessions() as db:
        row = NotificationOutbox(
            dedupe_key="OPS-QA-T10-NO-RETRY",
            notification_type="INCIDENT_OPEN",
            channel="SLACK",
            state=NotificationOutboxState.SENT,
            payload=_intent("ignored").message.payload(),
            fallback_text="운영 알림",
            attempt_count=1,
            max_attempts=3,
            next_attempt_at=None,
            sent_at=_NOW,
            version=2,
        )
        db.add(row)
        await db.commit()
        row_id = row.id

    # When: an operator requests retry from an illegal state/version
    async with outbox_sessions() as db:
        illegal = await retry_notification(
            db,
            row_id,
            expected_version=2,
            actor="ops@example.test",
            reason="잘못된 재시도",
            now=_NOW,
        )

    # Then: a typed conflict is returned and no state changes
    assert isinstance(illegal, NotificationRetryConflict)
    assert illegal.code == "NOTIFICATION_RETRY_STATE_CONFLICT"


def test_notification_worker_is_included_routed_and_scheduled_every_minute() -> None:
    # Given/When: the deployed Celery configuration is resolved
    from app.core.celery_app import REDBEAT_SCHEDULE_VERSION, celery_app

    # Then: dispatcher import, default queue and one-minute recovery path stay aligned
    assert "app.workers.notification_tasks" in celery_app.conf.include
    assert celery_app.conf.task_routes["app.workers.notification_tasks.dispatch_notification_outbox"]["queue"] == "default"
    schedule = celery_app.conf.beat_schedule["dispatch-notification-outbox"]
    assert schedule["task"] == "app.workers.notification_tasks.dispatch_notification_outbox"
    assert schedule["schedule"].minute == set(range(60))
    assert REDBEAT_SCHEDULE_VERSION >= "2026-08-10.1"


@pytest.mark.asyncio
async def test_notification_worker_reconciles_sent_publish_hooks_every_tick(monkeypatch) -> None:
    sessions = object()
    observed: list[tuple[str, object]] = []

    async def fake_dispatch(sessionmaker, _client, **_kwargs):
        observed.append(("dispatch", sessionmaker))
        return DispatchResult(claimed=1, sent=1)

    async def fake_reconcile(sessionmaker):
        observed.append(("reconcile", sessionmaker))
        return 2

    async def fake_incident_reconcile(sessionmaker):
        observed.append(("incident_reconcile", sessionmaker))
        return 1

    monkeypatch.setattr(notification_tasks, "get_async_sessionmaker", lambda: sessions)
    monkeypatch.setattr(notification_tasks, "dispatch_notification_batch", fake_dispatch)
    monkeypatch.setattr(
        notification_tasks,
        "reconcile_sent_publish_notifications",
        fake_reconcile,
    )
    monkeypatch.setattr(
        notification_tasks,
        "reconcile_sent_notification_incidents",
        fake_incident_reconcile,
    )

    result, reconciled, incidents_recovered = await notification_tasks._dispatch_once(
        "worker:test"
    )

    assert result == DispatchResult(claimed=1, sent=1)
    assert reconciled == 2
    assert incidents_recovered == 1
    assert observed == [
        ("dispatch", sessions),
        ("reconcile", sessions),
        ("incident_reconcile", sessions),
    ]


@pytest.mark.asyncio
async def test_sent_delivery_incident_is_recovered_by_periodic_reconciliation(
    outbox_sessions,
) -> None:
    async with outbox_sessions() as db:
        row = await enqueue_notification(db, _intent("OPS-QA-T10-SENT-RECONCILE"), now=_NOW)
        row.state = NotificationOutboxState.SENT
        row.sent_at = _NOW
        row.next_attempt_at = None
        older_incident = Incident(
            dedupe_key="OPS-QA-T10-SENT-RECONCILE-UNKNOWN",  # gitleaks:allow — test dedupe key
            incident_type="NOTIFICATION_DELIVERY_UNKNOWN",
            state=IncidentState.OPEN,
            severity="HIGH",
            customer_impact="Slack 수신 여부 불명",
            source_type="NOTIFICATION_OUTBOX",
            source_id=str(row.id),
            safe_error_code="DELIVERY_OUTCOME_UNKNOWN",
            next_action="Slack 수신 여부 확인",
            admin_path="/operations",
        )
        current_incident = Incident(
            dedupe_key="OPS-QA-T10-SENT-RECONCILE-INCIDENT",  # gitleaks:allow — test dedupe key
            incident_type="NOTIFICATION_DELIVERY_FAILED",
            state=IncidentState.OPEN,
            severity="HIGH",
            customer_impact="운영 알림 미전달",
            source_type="NOTIFICATION_OUTBOX",
            source_id=str(row.id),
            safe_error_code="DELIVERY_RETRY_EXHAUSTED",
            next_action="Slack 설정 확인 후 재시도",
            admin_path="/operations",
        )
        db.add_all((older_incident, current_incident))
        await db.flush()
        row.incident_id = current_incident.id
        incident_ids = (older_incident.id, current_incident.id)
        await db.commit()

    assert await reconcile_sent_notification_incidents(outbox_sessions) == 1

    async with outbox_sessions() as verify:
        incidents = [await verify.get(Incident, incident_id) for incident_id in incident_ids]
        assert all(incident is not None for incident in incidents)
        assert all(incident.state == IncidentState.RECOVERED for incident in incidents)
        assert all(incident.recovered_at == _NOW for incident in incidents)
