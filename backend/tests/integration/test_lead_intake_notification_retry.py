"""도입문의 접수 Slack 실패는 drain이 다시 보낸다(2026-10 감사 F12, Phase 1-9c-1)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.lead import SalesLead
from app.services import notifier
from app.services.inquiry_diagnosis import DIAGNOSIS_NOTE_NO_INPUT
from app.workers import lead_diagnosis_tasks as leadgen


async def _inquiry(session, *, status, created_at=None, source="INQUIRY"):
    lead = SalesLead(
        clinic_name="재발송의원", clinic_type="도입문의", contact="010-0000-0000",
        privacy=True, source=source, notification_status=status,
    )
    session.add(lead)
    await session.flush()
    if created_at is not None:
        lead.created_at = created_at
        await session.flush()
    return lead


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

    assert stale.id in [lead.id for lead in expired]
    assert fresh.id not in [lead.id for lead in expired]
    await pg_async_session.refresh(stale)
    assert stale.notification_status == "FAILED"


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
    opened = []

    class _Ctx:
        async def __aenter__(self):
            return pg_async_session

        async def __aexit__(self, *exc):
            return False

    async def capture(**kwargs):
        opened.append(kwargs)

    monkeypatch.setattr(leadgen, "get_async_sessionmaker", lambda: _Ctx)
    monkeypatch.setattr(leadgen, "open_ops_incident", capture)
    monkeypatch.setattr(leadgen.notify_lead_intake, "delay", lambda *_a: None)
    monkeypatch.setattr(leadgen.run_lead_diagnosis, "delay", lambda *_a: None)
    monkeypatch.setattr(leadgen.build_lead_report, "delay", lambda *_a: None)
    monkeypatch.setattr(leadgen.send_lead_report_email, "delay", lambda *_a: None)

    result = await leadgen._drain()

    mine = [item for item in opened if item["object_id"] == str(stale.id)]
    assert [item["incident_type"] for item in mine] == ["LEAD_INTAKE_NOTIFICATION_FAILED"]
    assert mine[0]["hospital_name"] == "도입문의"  # 병원명을 사고 제목에 싣지 않는다
    assert result["expired_intake_notices"] >= 1
