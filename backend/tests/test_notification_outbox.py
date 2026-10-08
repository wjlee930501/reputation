"""Durable Slack outbox behavior and PostgreSQL concurrency proofs."""

from __future__ import annotations

import json
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
from app.services import notification_delivery
from app.services.incident_types import (
    SLACK_DEVELOPER_CHANNEL,
    IncidentAudience,
    incident_audience,
)
from app.services.notification_delivery import _resolve_route
from app.services.notification_outbox import (
    ClaimedNotification,
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
from tests.db_env import require_db_url


def _database_url() -> str:
    return require_db_url("NOTIFICATION_OUTBOX_DATABASE_URL")


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
        incident_type="CONTENT_GENERATION_FAILED",
    )


def _urls(value: object) -> list[str]:
    if isinstance(value, dict):
        return [item for key, nested in value.items() for item in ([nested] if key == "url" else _urls(nested)) if isinstance(item, str)]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _urls(nested)]
    return []


@pytest.fixture
async def outbox_sessions():
    engine = create_async_engine(_database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as cleanup:
        await cleanup.execute(text("DELETE FROM incidents WHERE source_type='NOTIFICATION_OUTBOX' AND source_id IN (SELECT id::text FROM notification_outbox WHERE dedupe_key LIKE 'OPS-QA-T10-%')"))
        await cleanup.execute(text("DELETE FROM notification_outbox WHERE dedupe_key LIKE 'OPS-QA-T10-%'"))
        await cleanup.execute(text("DELETE FROM incidents WHERE dedupe_key LIKE 'OPS-QA-T10-%'"))
        await _delete_channel_incidents(cleanup)
        await cleanup.commit()
    try:
        yield sessions
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(text("DELETE FROM incidents WHERE source_type='NOTIFICATION_OUTBOX' AND source_id IN (SELECT id::text FROM notification_outbox WHERE dedupe_key LIKE 'OPS-QA-T10-%')"))
            await cleanup.execute(text("DELETE FROM notification_outbox WHERE dedupe_key LIKE 'OPS-QA-T10-%'"))
            await cleanup.execute(text("DELETE FROM incidents WHERE dedupe_key LIKE 'OPS-QA-T10-%'"))
            await _delete_channel_incidents(cleanup)
            await cleanup.commit()
        await engine.dispose()


async def _delete_channel_incidents(db) -> None:
    """채널 사고는 모든 전송의 경로를 바꾸므로 테스트 사이에 남기지 않는다(알림 행 포함)."""

    channel_incidents = (
        "SELECT id FROM incidents WHERE incident_type='NOTIFICATION_DELIVERY_FAILED' "
        "AND source_id IN ('SLACK','SLACK_DEV')"
    )
    await db.execute(text(f"DELETE FROM notification_outbox WHERE incident_id IN ({channel_incidents})"))
    await db.execute(text(f"DELETE FROM incidents WHERE id IN ({channel_incidents})"))


@pytest.mark.parametrize(
    "incident_type",
    [
        "BACKGROUND_TASK_FAILED",
        "OPERATION_TERMINAL_FAILED",
        "BROKER_UNAVAILABLE",
        "UNSAFE_STORED_DISPATCH",
        "NOTIFICATION_DELIVERY_FAILED",
        "NOTIFICATION_DELIVERY_UNKNOWN",
        "CACHE_REVALIDATION_FAILED",
        "MONTHLY_DOCTOR_PDF_BLOCKED",
    ],
)
def test_pure_infrastructure_incidents_leave_the_ae_channel(incident_type: str) -> None:
    # Given: an incident whose repair is not something an AE can perform in Admin
    projection = replace(_projection(), incident_type=incident_type)

    # When
    intent = build_open_incident_notification(projection, "https://admin.example.test")

    # Then: the intent is addressed to the developer channel
    assert incident_audience(incident_type) is IncidentAudience.DEVELOPER
    assert intent.channel == SLACK_DEVELOPER_CHANNEL


@pytest.mark.parametrize(
    "incident_type", ["CONTENT_GENERATION_FAILED", "MONTHLY_REPORT_BLOCKED", "", "BRAND_NEW_TYPE"]
)
def test_unregistered_incident_types_stay_on_the_operator_channel(incident_type: str) -> None:
    # Given / When: any incident type the registry does not mark as developer-only
    intent = build_open_incident_notification(
        replace(_projection(), incident_type=incident_type), "https://admin.example.test"
    )

    # Then: the AE channel is the default, so a new type is never silently hidden
    assert incident_audience(incident_type) is IncidentAudience.OPERATOR
    assert intent.channel == "SLACK"


def test_developer_rows_route_to_the_operator_channel_when_no_developer_webhook() -> None:
    # Given: single-channel mode (no developer webhook) and two-channel mode
    single = {"SLACK": "https://ops.example.test", "SLACK_DEV": ""}
    both = {"SLACK": "https://ops.example.test", "SLACK_DEV": "https://dev.example.test"}

    # When / Then: developer rows reach a human on the operator channel, visibly marked
    route = _resolve_route("SLACK_DEV", None, single, set())
    assert (route.url, route.channel, route.marker) == ("https://ops.example.test", "SLACK", "[개발 확인]")
    assert _resolve_route("SLACK_DEV", None, both, set()).url == "https://dev.example.test"
    assert _resolve_route("SLACK_DEV", None, both, set()).marker is None
    assert _resolve_route("SLACK", None, both, set()).url == "https://ops.example.test"


def test_a_dead_channel_reroutes_to_the_other_webhook_or_holds_without_one() -> None:
    both = {"SLACK": "https://ops.example.test", "SLACK_DEV": "https://dev.example.test"}
    single = {"SLACK": "https://ops.example.test", "SLACK_DEV": ""}

    rerouted = _resolve_route("SLACK_DEV", None, both, {"SLACK_DEV"})
    assert (rerouted.url, rerouted.channel, rerouted.marker) == (
        "https://ops.example.test",
        "SLACK",
        "[채널 대체 전송]",
    )
    # 운영 채널이 죽었는데 다른 채널이 없다 = 진짜 장애. 보류한다.
    assert _resolve_route("SLACK", None, single, {"SLACK"}).url is None
    # 두 채널이 모두 죽었다.
    assert _resolve_route("SLACK", None, both, {"SLACK", "SLACK_DEV"}).url is None


def test_a_delivery_incident_is_never_notified_through_the_channel_it_describes() -> None:
    both = {"SLACK": "https://ops.example.test", "SLACK_DEV": "https://dev.example.test"}

    # 개발 채널을 말하는 사고의 알림(개발 담당)은 운영 채널로 간다 — 채널이 아직 건강해도.
    route = _resolve_route("SLACK_DEV", "SLACK_DEV", both, set())
    assert (route.url, route.marker) == ("https://ops.example.test", "[채널 대체 전송]")
    # 다른 채널이 없으면 같은 채널이 사람에게 닿는 유일한 길이다.
    single = {"SLACK": "https://ops.example.test", "SLACK_DEV": ""}
    assert _resolve_route("SLACK_DEV", "SLACK", single, set()).url == "https://ops.example.test"


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def commit(self):
        return None


def _isolate_dispatch(monkeypatch, row, decisions=None) -> None:
    async def recover(*_args, **_kwargs):
        return 0

    async def claim(*_args, **_kwargs):
        return (row,)

    async def finalize(_db, claimed, decision, _now, **_kwargs):
        if decisions is not None:
            decisions.append((claimed, decision))
        return True

    async def no_health(*_args, **_kwargs):
        return {}

    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(notification_delivery, "recover_stale_sending", recover)
    monkeypatch.setattr(notification_delivery, "claim_notification_batch", claim)
    monkeypatch.setattr(notification_delivery, "_finalize", finalize)
    monkeypatch.setattr(notification_delivery, "refresh_channel_health", no_health)
    monkeypatch.setattr(notification_delivery, "described_channels", no_health)
    monkeypatch.setattr(notification_delivery, "recover_after_send", nothing)
    monkeypatch.setattr(notification_delivery, "requeue_channel_held", nothing)
    monkeypatch.setattr(notification_delivery, "run_notification_success_hook", nothing)


@pytest.mark.asyncio
async def test_empty_developer_webhook_delivers_to_operator_with_developer_marker(
    monkeypatch,
) -> None:
    row = ClaimedNotification(
        uuid.uuid4(),
        None,
        None,
        None,
        {
            "text": "[Error : 오류 발생] [조치 필요] 시스템 · 백그라운드 작업 실패",
            "blocks": [
                {"type": "header", "block_id": "header", "text": {"type": "plain_text", "text": "[Error : 오류 발생] [조치 필요] 시스템"}},
                {"type": "section", "block_id": "body", "text": {"type": "mrkdwn", "text": "본문"}},
            ],
        },
        1,
        3,
        "worker",
        1,
        SLACK_DEVELOPER_CHANNEL,
    )
    _isolate_dispatch(monkeypatch, row)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="ok")

    operator_url = "https://hooks.slack.com/services/OPERATOR/ONLY/X"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await dispatch_notification_batch(
            _FakeSession,
            client,
            webhook_url=operator_url,
            developer_webhook_url="",
            worker_id="worker-single-channel",
            now=_NOW,
        )

    assert (result.sent, result.held) == (1, 0)
    assert [str(request.url) for request in requests] == [operator_url]
    body = json.loads(requests[0].content)
    assert body["text"].startswith("[Error : 오류 발생] [개발 확인] [조치 필요]")
    assert body["blocks"][0]["text"]["text"] == "[Error : 오류 발생] [개발 확인] [조치 필요] 시스템"
    assert body["blocks"][1]["text"]["text"] == "본문"
    # 저장된 payload는 그대로다 — 표시는 전송 사본에만 붙는다.
    assert row.payload["text"].startswith("[Error : 오류 발생] [조치 필요]")


@pytest.mark.asyncio
async def test_configured_developer_webhook_uses_only_developer_url(monkeypatch) -> None:
    row = _claimed(SLACK_DEVELOPER_CHANNEL)
    _isolate_dispatch(monkeypatch, row)

    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, text="ok")

    developer_url = "https://hooks.slack.com/services/DEVELOPER/ONLY/X"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await dispatch_notification_batch(
            _FakeSession,
            client,
            webhook_url="https://hooks.slack.com/services/OPERATOR/ONLY/X",
            developer_webhook_url=developer_url,
            worker_id="worker-dev-webhook-configured",
            now=_NOW,
        )

    assert (result.sent, result.held) == (1, 0)
    assert requested_urls == [developer_url]


def _claimed(channel: str) -> ClaimedNotification:
    return ClaimedNotification(
        uuid.uuid4(), None, None, None, {"text": "운영 알림"}, 1, 3, "worker", 1, channel
    )


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
        for label in ("조치 필요", "장편한외과의원", "콘텐츠에서")
    )
    assert "참조 OPS-" in summary_a.message.payload_json()
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
    assert "처리 기한 오늘 18:00" in open_intent.message.payload_json()
    assert "SLA:" not in open_intent.message.payload_json()
    assert "멈춘 글 확인" in open_intent.message.payload_json()
    recovered_payload = recovered.message.payload_json()
    assert "정상 복구가 확인됐습니다" in recovered_payload
    # Recovery is informational: it must never ask a person to confirm machine work.
    assert "추가 조치가 필요하지 않습니다" in recovered_payload
    assert "확인 완료’ 처리하세요" not in recovered_payload
    assert "복구 기록 보기" in recovered_payload
    assert "처리 기한" not in recovered_payload
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
        for label in ("조치 필요", "장편한외과의원", "운영센터에서 담당 항목")
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
async def test_long_fallback_text_is_stored_trimmed_and_full_text_stays_in_payload(
    outbox_sessions,
) -> None:
    # Given: a daily summary whose preview text is longer than the 1000-char column
    long_text = "가" * 1500
    intent = _intent("OPS-QA-LONG-FALLBACK")
    intent = replace(intent, message=replace(intent.message, fallback_text=long_text))

    # When: it is enqueued (this used to fail with StringDataRightTruncation)
    async with outbox_sessions() as db:
        row = await enqueue_notification(db, intent, now=_NOW)
        await db.flush()

        # Then: the column keeps a trimmed preview and the payload keeps the full text
        assert len(row.fallback_text) == 1000
        assert row.fallback_text.endswith("…")
        assert row.payload["text"] == long_text
        await db.rollback()


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
        # 행이 말하는 사고(없음)는 그대로 두고, 수신 불명 사고는 source_id로 행을 가리킨다.
        assert row.incident_id is None
        incident = await verify.scalar(
            select(Incident).where(Incident.source_id == str(row.id))
        )
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
        assert rows["OPS-QA-T10-400"].provider_response == {
            "http_status": 400,
            "body_code": "invalid_payload",
            "channel_used": "SLACK",  # 실제로 보낸 채널(수신 불명 판정이 이것으로 맞춘다)
        }
        delivery_incident = await verify.scalar(
            select(Incident).where(
                Incident.source_type == "NOTIFICATION_OUTBOX",
                Incident.source_id == str(rows["OPS-QA-T10-400"].id),
            )
        )
        assert delivery_incident is not None
        assert rows["OPS-QA-T10-400"].incident_id is None  # 실패가 행의 사고 연결을 덮지 않는다
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
        assert row.incident_id is None
        incident = await verify.scalar(
            select(Incident).where(Incident.source_id == str(row.id))
        )
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
async def test_notification_worker_only_reconciles_delivery_incidents_every_tick(monkeypatch) -> None:
    sessions = object()
    observed: list[tuple[str, object]] = []

    async def fake_dispatch(sessionmaker, _client, **_kwargs):
        observed.append(("dispatch", sessionmaker))
        return DispatchResult(claimed=1, sent=1)

    async def fake_incident_reconcile(sessionmaker):
        observed.append(("incident_reconcile", sessionmaker))
        return 1

    monkeypatch.setattr(notification_tasks, "get_async_sessionmaker", lambda: sessions)
    monkeypatch.setattr(notification_tasks, "dispatch_notification_batch", fake_dispatch)
    monkeypatch.setattr(
        notification_tasks,
        "reconcile_sent_notification_incidents",
        fake_incident_reconcile,
    )

    result, incidents_recovered = await notification_tasks._dispatch_once(
        "worker:test"
    )

    assert result == DispatchResult(claimed=1, sent=1)
    assert incidents_recovered == 1
    assert observed == [
        ("dispatch", sessions),
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


@pytest.mark.asyncio
async def test_a_redirect_is_one_channel_configuration_incident_not_a_delivery_check(
    outbox_sessions,
) -> None:
    """302는 Slack이 받지 않았다는 뜻이다 — '수신 여부 확인' 사고를 알림마다 열지 않는다(2026-10-02).

    운영 채널 하나뿐인데 그 웹훅이 죽었다 = 보낼 곳이 없다. 알림은 버리지 않고 보류한다.
    """

    posts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal posts
        posts += 1
        return httpx.Response(302, headers={"Location": "https://example.invalid/"})

    await _dispatch_once(outbox_sessions, "OPS-QA-T10-REDIRECT-1", handler)
    await _dispatch_once(outbox_sessions, "OPS-QA-T10-REDIRECT-2", handler)

    assert posts == 1  # 두 번째 알림은 죽은 채널로 다시 보내지 않는다(탐침은 한 시간 뒤)
    async with outbox_sessions() as verify:
        rows = (
            await verify.execute(
                select(NotificationOutbox).where(
                    NotificationOutbox.dedupe_key.in_(("OPS-QA-T10-REDIRECT-1", "OPS-QA-T10-REDIRECT-2"))
                )
            )
        ).scalars().all()
        assert {row.state for row in rows} == {NotificationOutboxState.HOLD}
        assert {row.safe_error_code for row in rows} == {"SLACK_CHANNEL_UNAVAILABLE"}
        assert {row.incident_id for row in rows} == {None}
        channel_incidents = (
            await verify.execute(
                select(Incident).where(
                    Incident.incident_type == "NOTIFICATION_DELIVERY_FAILED",
                    Incident.source_id.in_(("SLACK", "SLACK_DEV")),
                )
            )
        ).scalars().all()
        assert len(channel_incidents) == 1  # 채널 하나의 설정 오류 사고 하나
        assert channel_incidents[0].source_id == "SLACK"
        assert channel_incidents[0].hospital_id is None
        unknown = await verify.scalar(
            select(func.count(Incident.id)).where(
                Incident.incident_type == "NOTIFICATION_DELIVERY_UNKNOWN",
                Incident.source_id.in_([str(row.id) for row in rows]),
            )
        )
        assert unknown == 0


async def _seed(outbox_sessions, key: str, *, channel: str = "SLACK") -> uuid.UUID:
    async with outbox_sessions() as db:
        row = await enqueue_notification(db, replace(_intent(key), channel=channel), now=_NOW)
        await db.commit()
        return row.id


async def _dispatch_two_channels(outbox_sessions, handler, *, now: datetime, developer_url: str):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await dispatch_notification_batch(
            outbox_sessions,
            client,
            webhook_url=_OPS_URL,
            developer_webhook_url=developer_url,
            worker_id=f"worker-{uuid.uuid4().hex}",
            now=now,
            limit=10,
        )


_OPS_URL = "https://hooks.slack.com/services/T/OPS/X"
_DEV_URL = "https://hooks.slack.com/services/T/DEV/X"


async def _channel_incident(db, channel: str) -> Incident | None:
    return await db.scalar(
        select(Incident)
        .where(
            Incident.incident_type == "NOTIFICATION_DELIVERY_FAILED",
            Incident.source_id == channel,
        )
        .execution_options(populate_existing=True)
    )


@pytest.mark.asyncio
async def test_dead_developer_webhook_opens_one_channel_incident_noticed_on_the_operator_channel(
    outbox_sessions,
) -> None:
    # Given: the developer webhook answers 302 (the 2026-09-19 production fact)
    first = await _seed(outbox_sessions, "OPS-QA-T10-DEVDEAD-1", channel="SLACK_DEV")
    second = await _seed(outbox_sessions, "OPS-QA-T10-DEVDEAD-2", channel="SLACK_DEV")
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        if str(request.url) == _DEV_URL:
            return httpx.Response(302, headers={"Location": "https://example.invalid/"})
        return httpx.Response(200, text="ok")

    # When: two dispatcher ticks run
    await _dispatch_two_channels(outbox_sessions, handler, now=_NOW, developer_url=_DEV_URL)
    await _dispatch_two_channels(
        outbox_sessions, handler, now=_NOW + timedelta(seconds=5), developer_url=_DEV_URL
    )

    # Then: the developer webhook got exactly one POST; everything else went to ops
    dev_posts = [url for url, _ in seen if url == _DEV_URL]
    assert len(dev_posts) == 1
    ops_bodies = [body for url, body in seen if url == _OPS_URL]
    async with outbox_sessions() as verify:
        incident = await _channel_incident(verify, "SLACK_DEV")
        assert incident is not None and incident.state == IncidentState.OPEN
        notice = await verify.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.incident_id == incident.id,
                NotificationOutbox.notification_type == "INCIDENT_OPEN",
            )
        )
        assert notice is not None and notice.state == NotificationOutboxState.SENT
        rows = {
            row.id: row
            for row in (
                await verify.execute(
                    select(NotificationOutbox).where(NotificationOutbox.id.in_((first, second)))
                )
            ).scalars()
        }
        assert {row.state for row in rows.values()} == {NotificationOutboxState.SENT}
        assert {row.incident_id for row in rows.values()} == {None}
    # 채널 사고 알림과 재전송된 개발 알림 모두 운영 채널에 '채널 대체 전송' 표시로 갔다.
    assert len(ops_bodies) == 3
    assert all("[채널 대체 전송]" in body["text"] for body in ops_bodies)
    notice_texts = [body["text"] for body in ops_bodies if body["text"].startswith("[Error")]
    assert notice_texts and notice_texts[0].startswith("[Error : 오류 발생] [채널 대체 전송]")


@pytest.mark.asyncio
async def test_hourly_probe_recovers_the_channel_and_requeues_held_rows(outbox_sessions) -> None:
    # Given: a single-channel operator webhook went dead and one row is held
    held = await _seed(outbox_sessions, "OPS-QA-T10-PROBE-1")
    dead = lambda _request: httpx.Response(404, text="no_service")  # noqa: E731
    await _dispatch_once(outbox_sessions, "OPS-QA-T10-PROBE-1", dead)
    probes: list[dict] = []

    def alive(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body == {}:
            probes.append(body)
            return httpx.Response(400, text="no_text")
        return httpx.Response(200, text="ok")

    # When: a tick inside the hour does not probe; a tick after the hour does
    async with httpx.AsyncClient(transport=httpx.MockTransport(alive)) as client:
        early = await dispatch_notification_batch(
            outbox_sessions, client, webhook_url="https://hooks.slack.com/services/T/B/X",
            worker_id="w-early", now=_NOW + timedelta(minutes=30), limit=10,
        )
        later = await dispatch_notification_batch(
            outbox_sessions, client, webhook_url="https://hooks.slack.com/services/T/B/X",
            worker_id="w-later", now=_NOW + timedelta(hours=1, minutes=1), limit=10,
        )

    # Then: one probe, the channel incident recovered, the held row was delivered
    assert early.sent == 0
    assert len(probes) == 1
    assert later.sent >= 1
    async with outbox_sessions() as verify:
        incident = await _channel_incident(verify, "SLACK")
        assert incident is not None
        assert incident.state == IncidentState.ACKNOWLEDGED
        assert incident.recovered_at == _NOW + timedelta(hours=1, minutes=1)
        row = await verify.get(NotificationOutbox, held)
        assert row is not None and row.state == NotificationOutboxState.SENT
        assert row.provider_response["channel_used"] == "SLACK"
        # 채널이 죽어 보낼 곳이 없던 동안 보류된 채널 사고 자신의 열림 알림은, 사고가 이미
        # 복구됐으므로 지난 사실이다 — 다시 보내지 않고, 전달되지 않은 열림에 복구를 짝짓지 않는다.
        notices = {
            notice.notification_type: notice
            for notice in (
                await verify.execute(
                    select(NotificationOutbox).where(NotificationOutbox.incident_id == incident.id)
                )
            ).scalars()
        }
        assert notices["INCIDENT_OPEN"].state == NotificationOutboxState.FAILED
        assert notices["INCIDENT_OPEN"].safe_error_code == "STALE_NOT_RESENT"
        assert "INCIDENT_RECOVERED" not in notices


@pytest.mark.asyncio
async def test_dead_probe_keeps_the_incident_and_waits_another_hour(outbox_sessions) -> None:
    await _seed(outbox_sessions, "OPS-QA-T10-PROBE-DEAD")
    redirect = lambda _request: httpx.Response(302, headers={"Location": "https://x.invalid/"})  # noqa: E731
    await _dispatch_once(outbox_sessions, "OPS-QA-T10-PROBE-DEAD", redirect)
    posts = 0

    def still_dead(_request: httpx.Request) -> httpx.Response:
        nonlocal posts
        posts += 1
        return httpx.Response(302, headers={"Location": "https://x.invalid/"})

    for minutes in (61, 62, 90):
        await _dispatch_once(
            outbox_sessions, "OPS-QA-T10-PROBE-DEAD", still_dead, now=_NOW + timedelta(minutes=minutes)
        )

    assert posts == 1  # 61분의 탐침 한 번. 62·90분은 마지막 관측에서 한 시간이 안 됐다.
    async with outbox_sessions() as verify:
        incident = await _channel_incident(verify, "SLACK")
        assert incident is not None and incident.state == IncidentState.OPEN


@pytest.mark.asyncio
async def test_single_channel_mode_closes_the_old_developer_channel_incident(outbox_sessions) -> None:
    # Given: the production state — an open SLACK_DEV channel incident — then dev webhook emptied
    await _seed(outbox_sessions, "OPS-QA-T10-SINGLE-1", channel="SLACK_DEV")
    await _dispatch_two_channels(
        outbox_sessions,
        lambda request: httpx.Response(302, headers={"Location": "https://x.invalid/"})
        if str(request.url) == _DEV_URL
        else httpx.Response(200, text="ok"),
        now=_NOW,
        developer_url=_DEV_URL,
    )
    developer_row = await _seed(outbox_sessions, "OPS-QA-T10-SINGLE-2", channel="SLACK_DEV")
    seen: list[dict] = []

    def ops(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == _OPS_URL
        seen.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    # When: the next tick runs with SLACK_WEBHOOK_URL_DEV="" (single-channel mode)
    await _dispatch_two_channels(
        outbox_sessions, ops, now=_NOW + timedelta(minutes=1), developer_url=""
    )

    # Then: the dev channel incident is closed without a probe and developer rows carry [개발 확인]
    async with outbox_sessions() as verify:
        incident = await _channel_incident(verify, "SLACK_DEV")
        assert incident is not None and incident.state == IncidentState.ACKNOWLEDGED
        row = await verify.get(NotificationOutbox, developer_row)
        assert row is not None and row.state == NotificationOutboxState.SENT
    assert any("[개발 확인]" in body["text"] for body in seen)


@pytest.mark.asyncio
async def test_a_successful_send_on_the_channel_recovers_its_incident(outbox_sessions) -> None:
    # Given: an acknowledged-then-reopened style state — the channel incident is OPEN but the
    # dispatcher has no other channel, so routing holds; a person fixed the webhook and the
    # probe interval has passed with Slack accepting real deliveries
    from app.services.notification_channel_health import recover_after_send

    await _seed(outbox_sessions, "OPS-QA-T10-SENDREC")
    await _dispatch_once(
        outbox_sessions, "OPS-QA-T10-SENDREC", lambda _r: httpx.Response(403, text="invalid_token")
    )
    async with outbox_sessions() as db:
        assert (await _channel_incident(db, "SLACK")).state == IncidentState.OPEN

    await recover_after_send(outbox_sessions, "SLACK", now=_NOW + timedelta(minutes=2))

    async with outbox_sessions() as verify:
        incident = await _channel_incident(verify, "SLACK")
        assert incident is not None and incident.state == IncidentState.ACKNOWLEDGED
        recovered_notice = await verify.scalar(
            select(func.count(NotificationOutbox.id)).where(
                NotificationOutbox.incident_id == incident.id,
                NotificationOutbox.notification_type == "INCIDENT_RECOVERED",
            )
        )
        # 다른 채널이 없어 열림 알림은 전달되지 못했다 — 전달되지 않은 열림에 복구를 짝짓지 않는다.
        assert recovered_notice == 0


@pytest.mark.asyncio
async def test_a_delivered_open_notice_is_paired_with_a_recovered_notice(outbox_sessions) -> None:
    await _seed(outbox_sessions, "OPS-QA-T10-PAIR", channel="SLACK_DEV")
    dev_dead = True

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == _DEV_URL:
            if json.loads(request.content) == {}:
                return httpx.Response(400, text="no_text") if not dev_dead else httpx.Response(302)
            return httpx.Response(302) if dev_dead else httpx.Response(200, text="ok")
        return httpx.Response(200, text="ok")

    await _dispatch_two_channels(outbox_sessions, handler, now=_NOW, developer_url=_DEV_URL)
    await _dispatch_two_channels(
        outbox_sessions, handler, now=_NOW + timedelta(seconds=5), developer_url=_DEV_URL
    )
    dev_dead = False
    await _dispatch_two_channels(
        outbox_sessions, handler, now=_NOW + timedelta(hours=1, minutes=1), developer_url=_DEV_URL
    )

    async with outbox_sessions() as verify:
        incident = await _channel_incident(verify, "SLACK_DEV")
        assert incident.state == IncidentState.ACKNOWLEDGED
        recovered = await verify.scalar(
            select(func.count(NotificationOutbox.id)).where(
                NotificationOutbox.incident_id == incident.id,
                NotificationOutbox.notification_type == "INCIDENT_RECOVERED",
            )
        )
        assert recovered == 1


@pytest.mark.asyncio
async def test_held_rows_resend_only_when_fresh_and_unresolved(outbox_sessions) -> None:
    from app.services.notification_channel_health import requeue_channel_held

    ids: dict[str, uuid.UUID] = {}
    async with outbox_sessions() as db:
        resolved = Incident(
            dedupe_key="OPS-QA-T10-HELD-SUBJECT",
            incident_type="BACKGROUND_TASK_FAILED",
            state=IncidentState.ACKNOWLEDGED,
            severity="HIGH",
            customer_impact="테스트",
            source_type="TEST",
            next_action="테스트",
            admin_path="/operations",
            recovered_at=_NOW,
            acknowledged_at=_NOW,
        )
        db.add(resolved)
        await db.flush()
        for name, created, incident_id in (
            ("FRESH", _NOW - timedelta(hours=1), None),
            ("OLD", _NOW - timedelta(hours=25), None),
            ("RESOLVED", _NOW - timedelta(hours=1), resolved.id),
        ):
            row = await enqueue_notification(
                db, replace(_intent(f"OPS-QA-T10-HELD-{name}"), incident_id=incident_id), now=created
            )
            row.state = NotificationOutboxState.HOLD
            row.next_attempt_at = None
            row.safe_error_code = "SLACK_CHANNEL_UNAVAILABLE"
            ids[name] = row.id
        await db.commit()

    async with outbox_sessions() as db:
        assert await requeue_channel_held(db, now=_NOW) == 1
        await db.commit()

    async with outbox_sessions() as verify:
        rows = {name: await verify.get(NotificationOutbox, row_id) for name, row_id in ids.items()}
        assert rows["FRESH"].state == NotificationOutboxState.RETRYING
        assert rows["FRESH"].next_attempt_at == _NOW
        for name in ("OLD", "RESOLVED"):
            assert rows[name].state == NotificationOutboxState.FAILED
            assert rows[name].safe_error_code == "STALE_NOT_RESENT"


@pytest.mark.asyncio
async def test_a_flapping_channel_doubles_its_probe_interval_and_does_not_renotify_today(
    outbox_sessions,
) -> None:
    from app.services.notification_channel_health import probe_interval

    alive = False
    probes: list[datetime] = []
    clock = _NOW

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content) == {}:
            probes.append(clock)
            return httpx.Response(400, text="no_text") if alive else httpx.Response(302)
        return httpx.Response(200, text="ok") if alive else httpx.Response(302)

    async def tick(at: datetime, key: str | None = None) -> None:
        nonlocal clock
        clock = at
        if key:
            async with outbox_sessions() as db:
                await enqueue_notification(db, _intent(key), now=at)
                await db.commit()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await dispatch_notification_batch(
                outbox_sessions, client, webhook_url="https://hooks.slack.com/services/T/B/X",
                worker_id=f"w-{uuid.uuid4().hex}", now=at, limit=10,
            )

    # dies → recovers by probe → dies again within the hour (a flap)
    await tick(_NOW, "OPS-QA-T10-FLAP-1")
    alive = True
    await tick(_NOW + timedelta(hours=1, minutes=1))
    alive = False
    await tick(_NOW + timedelta(hours=1, minutes=10), "OPS-QA-T10-FLAP-2")

    async with outbox_sessions() as db:
        incident = await _channel_incident(db, "SLACK")
        assert incident.state == IncidentState.OPEN
        assert incident.episode_seq == 2
        assert await probe_interval(db, incident, _NOW + timedelta(hours=1, minutes=10)) == timedelta(hours=2)
        opens = await db.scalar(
            select(func.count(NotificationOutbox.id)).where(
                NotificationOutbox.incident_id == incident.id,
                NotificationOutbox.notification_type == "INCIDENT_OPEN",
            )
        )
        assert opens == 1  # 같은 KST 날의 두 번째 열림은 알리지 않는다

    # an hour later is still inside the doubled interval — no probe
    probes.clear()
    await tick(_NOW + timedelta(hours=2, minutes=20))
    assert probes == []
    await tick(_NOW + timedelta(hours=3, minutes=20))
    assert probes == [_NOW + timedelta(hours=3, minutes=20)]


@pytest.mark.asyncio
async def test_a_payload_rejection_stays_one_rows_failure_not_a_channel_incident(
    outbox_sessions,
) -> None:
    await _dispatch_once(
        outbox_sessions, "OPS-QA-T10-ROW400", lambda _r: httpx.Response(400, text="invalid_blocks")
    )
    async with outbox_sessions() as verify:
        assert await _channel_incident(verify, "SLACK") is None


@pytest.mark.asyncio
async def test_channel_cleanup_is_dry_run_by_default_and_closes_only_stale_rows(outbox_sessions) -> None:
    from app.utils.notification_channel_cleanup import run_cleanup

    # Given: the production shape — a dead SLACK_DEV channel incident, one held notice about an
    # incident already closed, and one held notice about a still-open incident
    await _seed(outbox_sessions, "OPS-QA-T10-CLEAN-DEAD", channel="SLACK_DEV")
    await _dispatch_two_channels(
        outbox_sessions,
        lambda request: httpx.Response(302, headers={"Location": "https://x.invalid/"})
        if str(request.url) == _DEV_URL
        else httpx.Response(200, text="ok"),
        now=_NOW,
        developer_url=_DEV_URL,
    )
    ids: dict[str, uuid.UUID] = {}
    async with outbox_sessions() as db:
        # OVERWRITTEN: PR 전 실패가 incident_id를 (이미 닫힌) 전송 사고로 덮어쓴 FAILED 행 —
        # 그 알림이 말하던 사고를 알 수 없으므로 '지난 알림'으로 종결하지 않는다.
        for name, state in (
            ("CLOSED", IncidentState.ACKNOWLEDGED),
            ("OPEN", IncidentState.OPEN),
            ("OVERWRITTEN", IncidentState.ACKNOWLEDGED),
        ):
            subject = Incident(
                dedupe_key=f"OPS-QA-T10-CLEAN-SUBJECT-{name}",
                incident_type=(
                    "NOTIFICATION_DELIVERY_FAILED" if name == "OVERWRITTEN" else "BACKGROUND_TASK_FAILED"
                ),
                state=state,
                severity="HIGH",
                customer_impact="테스트",
                source_type="TEST",
                next_action="테스트",
                admin_path="/operations",
                recovered_at=_NOW if state == IncidentState.ACKNOWLEDGED else None,
                acknowledged_at=_NOW if state == IncidentState.ACKNOWLEDGED else None,
            )
            db.add(subject)
            await db.flush()
            row = await enqueue_notification(
                db,
                replace(_intent(f"OPS-QA-T10-CLEAN-{name}"), channel="SLACK_DEV", incident_id=subject.id),
                now=_NOW,
            )
            overwritten = name == "OVERWRITTEN"
            row.state = NotificationOutboxState.FAILED if overwritten else NotificationOutboxState.HOLD
            row.next_attempt_at = None
            row.safe_error_code = "WEBHOOK_URL_REJECTED" if overwritten else "DEV_WEBHOOK_MISSING"
            ids[name] = row.id
        await db.commit()

    # When: dry-run, then --confirm with the developer webhook emptied
    dry = await run_cleanup(outbox_sessions, confirm=False, developer_webhook_url="", now=_NOW)
    async with outbox_sessions() as verify:
        assert (await verify.get(NotificationOutbox, ids["CLOSED"])).state == NotificationOutboxState.HOLD
        assert (await _channel_incident(verify, "SLACK_DEV")).state == IncidentState.OPEN
    applied = await run_cleanup(outbox_sessions, confirm=True, developer_webhook_url="", now=_NOW)

    # Then: only the stale row became terminal; the channel incident closed as single-channel mode
    assert dry["stale_developer_rows"] == 1 and dry["developer_channel_incident_open"] is True
    assert dry["resent_developer_rows"] == 1
    assert dry["developer_channel_incident_resolved"] is False
    assert applied["developer_channel_incident_resolved"] is True
    async with outbox_sessions() as verify:
        closed = await verify.get(NotificationOutbox, ids["CLOSED"])
        still = await verify.get(NotificationOutbox, ids["OPEN"])
        assert closed.state == NotificationOutboxState.FAILED
        assert closed.safe_error_code == "STALE_NOT_RESENT"
        # 그 사고가 아직 열린 개발 알림은 운영 채널로 다시 보낸다(`[개발 확인]`).
        overwritten = await verify.get(NotificationOutbox, ids["OVERWRITTEN"])
        assert overwritten.safe_error_code == "WEBHOOK_URL_REJECTED"
        assert still.state == NotificationOutboxState.RETRYING
        assert still.safe_error_code is None
        assert (await _channel_incident(verify, "SLACK_DEV")).state == IncidentState.ACKNOWLEDGED
    # 개발 웹훅이 아직 설정돼 있으면 채널 사고는 닫지 않는다(탐침이 맡는다).
    again = await run_cleanup(
        outbox_sessions, confirm=True, developer_webhook_url=_DEV_URL, now=_NOW
    )
    assert again["stale_developer_rows"] == 0


@pytest.mark.asyncio
async def test_operations_center_and_retry_target_the_row_a_delivery_incident_describes(
    outbox_sessions,
) -> None:
    """전송 실패가 더는 행의 incident_id를 덮어쓰지 않으므로, 전송 사고는 source_id로 그 행을 찾는다."""

    from fastapi import HTTPException

    from app.api.admin.operations_center_incident_queries import _load_grouped_rows
    from app.api.admin.operations_center_retry_routes import _authorize_notification_retry
    from app.models.admin_user import AdminUser

    async with outbox_sessions() as db:
        owner, stranger = (
            AdminUser(
                email=f"ops-qa-t10-{uuid.uuid4().hex[:8]}@example.test",
                name=name,
                role="OPERATOR",
                password_hash="x",
            )
            for name in ("담당 운영자", "다른 운영자")
        )
        db.add_all((owner, stranger))
        await db.flush()
        subject = Incident(
            dedupe_key="OPS-QA-T10-LINK-SUBJECT",
            incident_type="BACKGROUND_TASK_FAILED",
            state=IncidentState.OPEN,
            severity="HIGH",
            customer_impact="테스트",
            source_type="TEST",
            next_action="테스트",
            admin_path="/operations",
        )
        db.add(subject)
        await db.flush()
        failed = await enqueue_notification(
            db, replace(_intent("OPS-QA-T10-LINK-ROW"), incident_id=subject.id), now=_NOW
        )
        failed.state = NotificationOutboxState.FAILED
        failed.next_attempt_at = None
        delivery = Incident(
            dedupe_key="OPS-QA-T10-LINK-DELIVERY",
            incident_type="NOTIFICATION_DELIVERY_FAILED",
            state=IncidentState.OPEN,
            severity="HIGH",
            customer_impact="테스트",
            source_type="NOTIFICATION_OUTBOX",
            source_id=str(failed.id),
            next_action="테스트",
            admin_path="/operations",
            owner_id=owner.id,
        )
        db.add(delivery)
        await db.flush()
        # 그 실패를 알리는 전송 사고의 열림 알림(사고의 incident_id 행)은 더 나중에 생긴다.
        await enqueue_notification(
            db,
            replace(_intent("OPS-QA-T10-LINK-NOTICE"), incident_id=delivery.id),
            now=_NOW + timedelta(minutes=1),
        )
        await db.flush()

        rows = await _load_grouped_rows(db, [delivery.id, subject.id], now=_NOW)
        slack_ids = {row.slack.notification_id for row in rows if row.slack is not None}
        # 전송 사고 → 실패한 그 행. 일반 사고 → 그 사고를 알리는 행(같은 행). 전송 사고의 열림
        # 알림 행은 어느 쪽에도 보이지 않는다.
        assert slack_ids == {failed.id}

        # 전송 사고의 담당자는 그 행을 재시도할 수 있고, 무관한 운영자는 못 한다.
        await _authorize_notification_retry(db, owner, failed)
        with pytest.raises(HTTPException):
            await _authorize_notification_retry(db, stranger, failed)
        await db.rollback()
