"""도입문의 접수 Slack 실패는 drain이 다시 보낸다(2026-10 감사 F12, Phase 1-9c-1)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.lead import SalesLead
from app.models.operations import Incident
from app.services import notifier
from app.services.inquiry_diagnosis import DIAGNOSIS_NOTE_NO_INPUT
from app.workers import lead_diagnosis_tasks as leadgen


async def _inquiry(
    session, *, status, created_at=None, source="INQUIRY", privacy=True, ack_sms_status=None
):
    lead = SalesLead(
        clinic_name="재발송의원", clinic_type="도입문의", contact="010-0000-0000",
        privacy=privacy, source=source, notification_status=status, ack_sms_status=ack_sms_status,
    )
    session.add(lead)
    await session.flush()
    if created_at is not None:
        lead.created_at = created_at
        await session.flush()
    return lead


async def _incidents(session, lead_id, kind="LEAD_INTAKE_NOTIFICATION_FAILED") -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Incident)
            .where(Incident.source_id == str(lead_id), Incident.incident_type == kind)
        )
    )


def _route_drain(session, monkeypatch):
    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(leadgen, "get_async_sessionmaker", lambda: _Ctx)
    monkeypatch.setattr(leadgen.notify_lead_intake, "delay", lambda *_a: None)
    monkeypatch.setattr(leadgen.run_lead_diagnosis, "delay", lambda *_a: None)
    monkeypatch.setattr(leadgen.build_lead_report, "delay", lambda *_a: None)
    monkeypatch.setattr(leadgen.send_lead_report_email, "delay", lambda *_a: None)


@pytest.mark.asyncio
async def test_drain_picks_only_explicit_pending_inquiries_within_the_window(pg_async_session):
    now = datetime.now(timezone.utc)
    pending = await _inquiry(pg_async_session, status="PENDING")
    in_flight = await _inquiry(pg_async_session, status=None)
    stale = await _inquiry(pg_async_session, status="PENDING", created_at=now - timedelta(hours=2))
    free_null = await _inquiry(pg_async_session, status=None, source="AI_DIAGNOSIS")

    picked = await leadgen._lead_notifications_to_dispatch(pg_async_session)

    assert str(pending.id) in picked
    assert str(in_flight.id) not in picked  # API가 아직 보내는 중일 수 있다
    assert str(stale.id) not in picked
    assert str(free_null.id) in picked  # 무료 진단 접수는 종전대로 NULL도 집는다


@pytest.mark.asyncio
async def test_expired_pending_inquiry_becomes_failed(pg_async_session):
    now = datetime.now(timezone.utc)
    stale = await _inquiry(pg_async_session, status="PENDING", created_at=now - timedelta(hours=2))
    fresh = await _inquiry(pg_async_session, status="PENDING")

    expired = await leadgen._expire_inquiry_notifications(pg_async_session, now=now)

    assert stale.id in expired
    assert fresh.id not in expired
    await pg_async_session.refresh(stale)
    assert stale.notification_status == "FAILED"
    assert await _incidents(pg_async_session, stale.id) == 1


@pytest.mark.asyncio
async def test_inquiry_resend_uses_the_inquiry_message_and_stays_pending_on_failure(
    pg_async_session, monkeypatch
):
    lead = await _inquiry(pg_async_session, status="PENDING")
    calls = []

    async def fake_created(**payload):
        calls.append(payload)
        return False

    monkeypatch.setattr(notifier, "notify_lead_created", fake_created)

    await leadgen._notify_lead_intake_in(pg_async_session, str(lead.id))

    assert calls and calls[0]["diagnosis_note"] == DIAGNOSIS_NOTE_NO_INPUT
    assert calls[0]["clinic_name"] == "재발송의원"
    status = await pg_async_session.scalar(
        select(SalesLead.notification_status).where(SalesLead.id == lead.id)
    )
    assert status == "PENDING"


@pytest.mark.asyncio
async def test_inquiry_resend_success_marks_sent(pg_async_session, monkeypatch):
    lead = await _inquiry(pg_async_session, status="PENDING")

    async def fake_created(**_payload):
        return True

    monkeypatch.setattr(notifier, "notify_lead_created", fake_created)

    result = await leadgen._notify_lead_intake_in(pg_async_session, str(lead.id))

    assert result["sent"] is True
    status = await pg_async_session.scalar(
        select(SalesLead.notification_status).where(SalesLead.id == lead.id)
    )
    assert status == "SENT"


@pytest.mark.asyncio
async def test_an_in_flight_inquiry_cannot_be_claimed_by_the_drain(pg_async_session, monkeypatch):
    lead = await _inquiry(pg_async_session, status=None)
    monkeypatch.setattr(
        notifier, "notify_lead_created", lambda **_k: pytest.fail("must not resend")
    )

    result = await leadgen._notify_lead_intake_in(pg_async_session, str(lead.id))

    assert result == {"skipped": "already_claimed_or_finished"}


@pytest.mark.asyncio
async def test_drain_opens_one_operator_incident_for_an_expired_notice(pg_async_session, monkeypatch):
    now = datetime.now(timezone.utc)
    stale = await _inquiry(pg_async_session, status="PENDING", created_at=now - timedelta(hours=2))
    _route_drain(pg_async_session, monkeypatch)

    result = await leadgen._drain()

    incident = (
        await pg_async_session.execute(
            select(Incident).where(
                Incident.source_id == str(stale.id),
                Incident.incident_type == "LEAD_INTAKE_NOTIFICATION_FAILED",
            )
        )
    ).scalars().all()
    assert len(incident) == 1
    assert incident[0].admin_path == "/leads"
    assert result["expired_intake_notices"] >= 1


@pytest.mark.asyncio
async def test_a_sending_inquiry_stuck_after_a_crash_expires_with_one_incident(
    pg_async_session, monkeypatch
):
    """워커가 SENDING으로 잡은 채 죽으면 drain이 집지 못한다 — 창+여유가 지나면 FAILED와 사고로 끝낸다."""
    now = datetime.now(timezone.utc)
    stuck = await _inquiry(pg_async_session, status="SENDING", created_at=now - timedelta(hours=2))
    fresh = await _inquiry(pg_async_session, status="SENDING")
    _route_drain(pg_async_session, monkeypatch)

    await leadgen._drain()

    await pg_async_session.refresh(stuck)
    await pg_async_session.refresh(fresh)
    assert stuck.notification_status == "FAILED"
    assert fresh.notification_status == "SENDING"
    assert await _incidents(pg_async_session, stuck.id) == 1
    assert await _incidents(pg_async_session, fresh.id) == 0


@pytest.mark.asyncio
async def test_a_purged_inquiry_is_not_expired(pg_async_session):
    now = datetime.now(timezone.utc)
    purged = await _inquiry(pg_async_session, status="PENDING", created_at=now - timedelta(hours=2))
    purged.purged_at = now
    await pg_async_session.flush()

    expired = await leadgen._expire_inquiry_notifications(pg_async_session, now=now)

    assert purged.id not in expired


# ── 사고 기록의 내구성(Codex HOLD P1): FAILED와 사고는 한 커밋이고, 틈이 생겨도 다음 tick이 메운다.


@pytest.mark.asyncio
async def test_a_crash_after_the_status_write_converges_to_exactly_one_incident(
    pg_async_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    stale = await _inquiry(pg_async_session, status="PENDING", created_at=now - timedelta(hours=2))
    await pg_async_session.commit()  # 접수는 이미 커밋돼 있다 — 롤백이 되돌리는 것은 만료뿐이다
    real_open = leadgen.open_ops_incident_in
    calls = {"n": 0}

    async def crash_once(db, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("process died after the status write")
        return await real_open(db, **kwargs)

    monkeypatch.setattr(leadgen, "open_ops_incident_in", crash_once)

    first = await leadgen._expire_inquiry_notifications(pg_async_session, now=now)
    await pg_async_session.refresh(stale)
    assert first == []
    assert stale.notification_status == "PENDING"  # 상태만 남지 않는다
    assert await _incidents(pg_async_session, stale.id) == 0

    second = await leadgen._expire_inquiry_notifications(pg_async_session, now=now)
    third = await leadgen._expire_inquiry_notifications(pg_async_session, now=now)
    await pg_async_session.refresh(stale)
    assert second == [stale.id] and third == []
    assert stale.notification_status == "FAILED"
    assert await _incidents(pg_async_session, stale.id) == 1


@pytest.mark.asyncio
async def test_drain_repairs_a_failed_notice_left_without_an_incident(pg_async_session, monkeypatch):
    """옛 코드나 사람 손으로 FAILED만 남은 행도 다음 tick이 사고 하나를 연다 — 두 번 열지 않는다."""
    now = datetime.now(timezone.utc)
    orphan = await _inquiry(pg_async_session, status="FAILED", created_at=now - timedelta(hours=3))
    old = await _inquiry(pg_async_session, status="FAILED", created_at=now - timedelta(days=8))
    taken = await _inquiry(pg_async_session, status="FAILED", created_at=now - timedelta(hours=3))
    taken.status = "CONTACTED"
    await pg_async_session.flush()
    _route_drain(pg_async_session, monkeypatch)

    first = await leadgen._drain()
    await leadgen._drain()

    assert await _incidents(pg_async_session, orphan.id) == 1
    assert await _incidents(pg_async_session, old.id) == 0  # 7일 창 밖
    assert await _incidents(pg_async_session, taken.id) == 0  # 사람이 이미 맡았다
    assert first["repaired_incidents"] >= 1


@pytest.mark.asyncio
async def test_drain_repairs_a_failed_sms_left_without_an_incident(pg_async_session, monkeypatch):
    now = datetime.now(timezone.utc)
    orphan = await _inquiry(
        pg_async_session, status="SENT", ack_sms_status="FAILED", created_at=now - timedelta(minutes=5)
    )
    skipped = await _inquiry(pg_async_session, status="SENT", ack_sms_status="SKIPPED")
    _route_drain(pg_async_session, monkeypatch)

    await leadgen._drain()
    await leadgen._drain()

    assert await _incidents(pg_async_session, orphan.id, "INQUIRY_SMS_FAILED") == 1
    assert await _incidents(pg_async_session, skipped.id, "INQUIRY_SMS_FAILED") == 0


# ── 상태 NULL 회수(Codex HOLD P2): 접수 API가 상태를 쓰기 전에 죽은 공개 접수.


@pytest.mark.asyncio
async def test_a_stale_null_public_inquiry_is_claimable_and_a_fresh_one_is_not(pg_async_session):
    now = datetime.now(timezone.utc)
    stale_null = await _inquiry(pg_async_session, status=None, created_at=now - timedelta(minutes=20))
    fresh_null = await _inquiry(pg_async_session, status=None, created_at=now - timedelta(minutes=5))
    outbound = await _inquiry(
        pg_async_session, status=None, created_at=now - timedelta(minutes=20), privacy=False
    )

    picked = await leadgen._lead_notifications_to_dispatch(pg_async_session)

    assert str(stale_null.id) in picked
    assert str(fresh_null.id) not in picked  # API가 아직 보내는 중일 수 있다
    assert str(outbound.id) not in picked  # Admin이 만든 리드는 접수 알림 대상이 아니다


@pytest.mark.asyncio
async def test_a_stale_null_inquiry_is_resent_once(pg_async_session, monkeypatch):
    now = datetime.now(timezone.utc)
    lead = await _inquiry(pg_async_session, status=None, created_at=now - timedelta(minutes=20))
    calls = []

    async def fake_created(**payload):
        calls.append(payload)
        return True

    monkeypatch.setattr(notifier, "notify_lead_created", fake_created)

    first = await leadgen._notify_lead_intake_in(pg_async_session, str(lead.id))
    second = await leadgen._notify_lead_intake_in(pg_async_session, str(lead.id))

    assert first["sent"] is True and second == {"skipped": "already_claimed_or_finished"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_null_inquiry_past_the_window_expires_but_admin_outbound_is_left_alone(
    pg_async_session,
):
    now = datetime.now(timezone.utc)
    lost = await _inquiry(pg_async_session, status=None, created_at=now - timedelta(hours=2))
    outbound = await _inquiry(
        pg_async_session, status=None, created_at=now - timedelta(hours=2), privacy=False
    )

    expired = await leadgen._expire_inquiry_notifications(pg_async_session, now=now)

    assert lost.id in expired and outbound.id not in expired
    await pg_async_session.refresh(outbound)
    assert outbound.notification_status is None


# ── 만료와 실제 발송의 경합(Codex HOLD P2): 둘 다 CAS이고, SENDING은 여유를 두고 만료한다.


@pytest.mark.asyncio
async def test_a_sending_row_inside_the_margin_is_not_expired(pg_async_session):
    """59분에 집어 보내는 중인 행을 60분에 만료하지 않는다(창 1시간 + 여유 15분)."""
    now = datetime.now(timezone.utc)
    sending = await _inquiry(pg_async_session, status="SENDING", created_at=now - timedelta(minutes=70))

    expired = await leadgen._expire_inquiry_notifications(pg_async_session, now=now)

    assert sending.id not in expired
    await pg_async_session.refresh(sending)
    assert sending.notification_status == "SENDING"


@pytest.mark.asyncio
async def test_a_sent_commit_between_selection_and_expiry_stays_sent(pg_async_session):
    now = datetime.now(timezone.utc)
    lead = await _inquiry(pg_async_session, status="PENDING", created_at=now - timedelta(hours=2))
    candidates = await leadgen._expired_inquiry_candidates(pg_async_session, now)
    assert lead.id in candidates

    # 선택과 만료 사이에 발송 워커가 SENT를 커밋했다.
    lead.notification_status = "SENT"
    await pg_async_session.commit()

    assert await leadgen._expire_inquiry_notice(pg_async_session, lead.id, now) is False
    await pg_async_session.refresh(lead)
    assert lead.notification_status == "SENT"
    assert await _incidents(pg_async_session, lead.id) == 0


@pytest.mark.asyncio
async def test_a_sender_finishing_after_expiry_does_not_overwrite_failed(
    pg_async_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    lead = await _inquiry(pg_async_session, status="PENDING", created_at=now - timedelta(minutes=59))

    async def slow_send(**_payload):
        # 보내는 동안 시계가 흘러 만료 스윕이 이 행을 끝냈다.
        await leadgen._expire_inquiry_notifications(pg_async_session, now=now + timedelta(hours=2))
        return True

    monkeypatch.setattr(notifier, "notify_lead_created", slow_send)

    await leadgen._notify_lead_intake_in(pg_async_session, str(lead.id))

    await pg_async_session.refresh(lead)
    assert lead.notification_status == "FAILED"  # 발송기의 SENT는 no-op
    assert await _incidents(pg_async_session, lead.id) == 1


@pytest.mark.asyncio
async def test_the_intake_response_records_the_sms_incident_in_its_own_commit(pg_async_session):
    from app.services import inquiry_sms

    lead = await _inquiry(pg_async_session, status="SENT")
    await pg_async_session.commit()  # 리드 커밋이 먼저다(계약)
    lead.ack_sms_status = "FAILED"

    assert await inquiry_sms.record_failure_incident(pg_async_session, lead.id) is True
    await pg_async_session.commit()

    assert await _incidents(pg_async_session, lead.id, "INQUIRY_SMS_FAILED") == 1
    await pg_async_session.refresh(lead)
    assert lead.ack_sms_status == "FAILED"


# ── 접수 API의 발송 claim(Codex R2-1): SENDING을 커밋한 뒤에만 보낸다.


def _intake_body():
    from app.api.public import leads as leads_api

    return leads_api.LeadCreate(
        clinic_name="접수claim의원",
        clinic_type="도입문의",
        contact="010-1234-5678",
        question="문의",
        privacy=True,
        source_path="/contact",
    )


async def _call_intake(session, monkeypatch, *, notify):
    from types import SimpleNamespace

    from fastapi import BackgroundTasks

    from app.api.public import leads as leads_api
    from app.services import inquiry_sms

    async def skipped_sms(_db, lead):
        lead.ack_sms_status = "SKIPPED"
        return inquiry_sms.AckSmsOutcome("SKIPPED", "stub")

    monkeypatch.setattr(inquiry_sms, "acknowledge_inquiry", skipped_sms)
    monkeypatch.setattr(notifier, "notify_lead_created", notify)
    request = SimpleNamespace(
        headers=SimpleNamespace(get=lambda _k, default=None: default),
        client=SimpleNamespace(host="127.0.0.1"),
    )
    return await leads_api.create_lead.__wrapped__(
        request=request, body=_intake_body(), background_tasks=BackgroundTasks(), db=session
    )


async def _intake_lead(session) -> SalesLead:
    return (
        await session.execute(
            select(SalesLead)
            .where(SalesLead.clinic_name == "접수claim의원")
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _age(session, lead, delta):
    lead.created_at = datetime.now(timezone.utc) - delta
    await session.commit()


@pytest.mark.asyncio
async def test_a_crash_after_the_sending_claim_is_never_resent_and_expires_to_an_incident(
    pg_async_session, monkeypatch
):
    """보낸 직후(또는 보내는 도중) 접수 API가 죽었다 — 갔는지 모른다. 다시 보내지 않고 사고로 끝낸다."""

    async def died_while_sending(**_payload):
        raise RuntimeError("process died after the Slack POST")

    with pytest.raises(RuntimeError):
        await _call_intake(pg_async_session, monkeypatch, notify=died_while_sending)

    lead = await _intake_lead(pg_async_session)
    assert lead.notification_status == "SENDING"  # 발송 전에 커밋된 claim

    monkeypatch.setattr(notifier, "notify_lead_created", lambda **_k: pytest.fail("must not resend"))
    await _age(pg_async_session, lead, timedelta(minutes=20))
    assert str(lead.id) not in await leadgen._lead_notifications_to_dispatch(pg_async_session)
    assert await leadgen._notify_lead_intake_in(pg_async_session, str(lead.id)) == {
        "skipped": "already_claimed_or_finished"
    }

    await _age(pg_async_session, lead, timedelta(hours=2))
    expired = await leadgen._expire_inquiry_notifications(pg_async_session)

    assert lead.id in expired
    assert (await _intake_lead(pg_async_session)).notification_status == "FAILED"
    assert await _incidents(pg_async_session, lead.id) == 1


@pytest.mark.asyncio
async def test_a_crash_before_the_sending_claim_leaves_null_and_is_resent_after_the_grace(
    pg_async_session, monkeypatch
):
    """claim을 커밋하기 전에 죽었다 — 아무도 보내지 않았다. 15분 뒤 drain이 한 번 보낸다."""

    from app.services import inquiry_diagnosis

    async def died_before_claim(*_a, **_k):
        raise RuntimeError("process died while creating the diagnosis")

    monkeypatch.setattr(inquiry_diagnosis, "create_inquiry_diagnosis", died_before_claim)
    monkeypatch.setattr(
        "app.api.public.leads.LeadCreate.diagnosis_input", lambda _self: object()
    )
    with pytest.raises(RuntimeError):
        await _call_intake(
            pg_async_session, monkeypatch, notify=lambda **_k: pytest.fail("not reached")
        )

    lead = await _intake_lead(pg_async_session)
    assert lead.notification_status is None
    assert str(lead.id) not in await leadgen._lead_notifications_to_dispatch(pg_async_session)

    sent = []

    async def resend(**payload):
        sent.append(payload)
        return True

    monkeypatch.setattr(notifier, "notify_lead_created", resend)
    await _age(pg_async_session, lead, timedelta(minutes=20))
    assert str(lead.id) in await leadgen._lead_notifications_to_dispatch(pg_async_session)
    await leadgen._notify_lead_intake_in(pg_async_session, str(lead.id))
    await leadgen._notify_lead_intake_in(pg_async_session, str(lead.id))

    assert len(sent) == 1
    assert (await _intake_lead(pg_async_session)).notification_status == "SENT"


@pytest.mark.asyncio
async def test_the_normal_intake_marks_sent_and_the_drain_leaves_it_alone(
    pg_async_session, monkeypatch
):
    calls = []

    async def ok(**payload):
        calls.append(payload)
        return True

    response = await _call_intake(pg_async_session, monkeypatch, notify=ok)

    lead = await _intake_lead(pg_async_session)
    assert response["ok"] is True and len(calls) == 1
    assert lead.notification_status == "SENT" and lead.notification_error is None
    await _age(pg_async_session, lead, timedelta(minutes=20))
    assert str(lead.id) not in await leadgen._lead_notifications_to_dispatch(pg_async_session)


@pytest.mark.asyncio
async def test_a_failed_intake_send_is_left_pending_for_the_drain(pg_async_session, monkeypatch):
    async def down(**_payload):
        return False

    await _call_intake(pg_async_session, monkeypatch, notify=down)

    lead = await _intake_lead(pg_async_session)
    assert lead.notification_status == "PENDING"
    assert str(lead.id) in await leadgen._lead_notifications_to_dispatch(pg_async_session)


# ── 만료·메움 스윕은 다른 트랜잭션이 잡은 행을 기다리지 않는다(Codex R2-4, lead 쪽).
#    잠금 경합은 커밋된 행에서만 보이므로 이 둘은 실제 커밋과 두 번째 연결을 쓰고 스스로 지운다.

_LOCK_CLINIC = "잠금만료가상의원"


@pytest.fixture
async def committed_sessions():
    from sqlalchemy import delete, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.db_env import require_db_url

    url = require_db_url("INTEGRATION_DATABASE_URL")
    for prefix in ("postgresql+psycopg2://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def cleanup():
        async with sessions() as db:
            await db.execute(text("SET lock_timeout = '5s'"))
            await db.execute(delete(SalesLead).where(SalesLead.clinic_name == _LOCK_CLINIC))
            await db.commit()

    await cleanup()
    try:
        yield sessions
    finally:
        await cleanup()
        await engine.dispose()


async def _committed_leads(sessions, *statuses, age=timedelta(hours=2)):
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        leads = [
            SalesLead(
                clinic_name=_LOCK_CLINIC, clinic_type="도입문의", contact="010-0000-0000",
                privacy=True, source="INQUIRY", notification_status=status,
            )
            for status in statuses
        ]
        db.add_all(leads)
        await db.flush()
        for lead in leads:
            lead.created_at = now - age
        await db.commit()
        return [lead.id for lead in leads]


async def _status(sessions, lead_id):
    async with sessions() as db:
        return await db.scalar(select(SalesLead.notification_status).where(SalesLead.id == lead_id))


@pytest.mark.asyncio
async def test_expiry_skips_a_row_locked_elsewhere_and_finishes_the_others(
    committed_sessions, monkeypatch
):
    import time

    from sqlalchemy import text

    opened = []

    async def record_incident(_db, lead_id):
        opened.append(lead_id)

    monkeypatch.setattr(leadgen, "_open_intake_notice_incident", record_incident)
    first, locked, last = await _committed_leads(committed_sessions, "PENDING", "PENDING", "PENDING")

    async with committed_sessions() as holder:
        await holder.execute(
            select(SalesLead.id).where(SalesLead.id == locked).with_for_update()
        )
        async with committed_sessions() as sweep:
            # 예전처럼 잠긴 행을 기다리면 이 제한에 걸려 tick이 3초를 쓴다.
            await sweep.execute(text("SET lock_timeout = '3s'"))
            started = time.monotonic()
            expired = await leadgen._expire_inquiry_notifications(sweep)
            elapsed = time.monotonic() - started
        await holder.rollback()

    assert elapsed < 2
    assert set(expired) == {first, last}
    assert sorted(opened) == sorted([first, last])
    assert await _status(committed_sessions, locked) == "PENDING"  # 다음 tick이 끝낸다
    assert await _status(committed_sessions, first) == "FAILED"


@pytest.mark.asyncio
async def test_incident_repair_skips_a_row_locked_elsewhere(committed_sessions, monkeypatch):
    from sqlalchemy import text

    opened = []

    async def record_incident(_db, lead_id):
        opened.append(lead_id)

    monkeypatch.setattr(leadgen, "_open_intake_notice_incident", record_incident)
    free, locked = await _committed_leads(
        committed_sessions, "FAILED", "FAILED", age=timedelta(hours=3)
    )

    async with committed_sessions() as holder:
        await holder.execute(
            select(SalesLead.id).where(SalesLead.id == locked).with_for_update()
        )
        async with committed_sessions() as sweep:
            await sweep.execute(text("SET lock_timeout = '3s'"))
            await leadgen._repair_missing_lead_incidents(sweep)
        await holder.rollback()

    assert free in opened
    assert locked not in opened
