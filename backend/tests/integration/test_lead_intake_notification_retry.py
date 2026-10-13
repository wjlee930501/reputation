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
