"""리포트 메일 발송 (설계 T-8 · T-9 · §5-4).

여기서 지키는 것은 **한 사람에게 두 번 보내지 않는다**이다.
"발송 성공 → 커밋 실패 → 재시도"가 두 통이 되면, 그 사실을 우리는 알 수도 없다.
"""
import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin import leads as leads_api
from app.core.config import settings
from app.models.audit import AdminAuditLog
from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    DeliveryStatus,
    ExecutionStatus,
    LeadDelivery,
    LeadDiagnosis,
    ReportStatus,
)
from app.services import lead_delivery, mailer

_slot_sequence = itertools.count(500)


class _MailSpy:
    def __init__(self, *, fail_times=0, message_id="msg-1"):
        self.sent: list[dict] = []
        self.fail_times = fail_times
        self.message_id = message_id

    async def send_email(self, *, to, subject, html, idempotency_key, client=None):
        self.sent.append(
            {"to": to, "subject": subject, "html": html, "key": idempotency_key}
        )
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("resend_error_500:boom")
        return mailer.MailResult(provider_message_id=self.message_id)

    @property
    def keys(self):
        return [item["key"] for item in self.sent]


@pytest.fixture
def mail(monkeypatch):
    spy = _MailSpy()
    monkeypatch.setattr(mailer, "send_email", spy.send_email)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")
    return spy


async def _seed(
    session,
    *,
    email="doctor@example.com",
    report_status=ReportStatus.READY.value,
    delivery_status=DeliveryStatus.PENDING.value,
):
    lead = SalesLead(
        clinic_name="발송테스트의원",
        clinic_type="내과",
        contact="010-0000-0000",
        email=email,
        privacy=True,
        source="AI_DIAGNOSIS",
    )
    session.add(lead)
    await session.flush()

    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        applicant_email_hash=uuid.uuid4().hex,
        subject_phone_hash=uuid.uuid4().hex,
        subject_hospital_name="발송테스트의원",
        subject_region="수서역",
        slot_date=date(2026, 8, 28),
        slot_no=next(_slot_sequence),
        queries=[{"slot": 1, "kind": "진료과형", "text": "수서역 근처 내과 병원 추천해줘"}],
        requested_models={"openai": "m", "gemini": "g", "judge": "j"},
        repeat_count=3,
        execution_status=ExecutionStatus.SUCCEEDED.value,
        report_status=report_status,
        delivery_status=delivery_status,
    )
    session.add(diagnosis)
    await session.flush()
    return diagnosis


@pytest.mark.asyncio
class TestHappyPath:
    async def test_report_email_is_sent_once_and_recorded(self, pg_async_session, mail):
        diagnosis = await _seed(pg_async_session)
        result = await lead_delivery.deliver_report(pg_async_session, diagnosis)

        assert len(mail.sent) == 1
        assert mail.sent[0]["to"] == "doctor@example.com"
        assert diagnosis.delivery_status == DeliveryStatus.SENT.value

        delivery = (
            await pg_async_session.execute(
                select(LeadDelivery).where(LeadDelivery.diagnosis_id == diagnosis.id)
            )
        ).scalar_one()
        assert delivery.status == DeliveryStatus.SENT.value
        assert delivery.provider_message_id == "msg-1"
        assert delivery.sent_at is not None
        assert result["delivery_id"] == str(delivery.id)

    async def test_idempotency_key_is_the_delivery_row_id(self, pg_async_session, mail):
        """행 id를 키로 쓰면 재시도에서 그대로 재현된다 — 난수를 새로 만들면 재현이 안 된다."""
        diagnosis = await _seed(pg_async_session)
        await lead_delivery.deliver_report(pg_async_session, diagnosis)

        delivery = (
            await pg_async_session.execute(
                select(LeadDelivery).where(LeadDelivery.diagnosis_id == diagnosis.id)
            )
        ).scalar_one()
        assert mail.keys == [str(delivery.id)]

    async def test_email_links_to_the_report(self, pg_async_session, mail):
        diagnosis = await _seed(pg_async_session)
        await lead_delivery.deliver_report(pg_async_session, diagnosis)
        assert "/ai-diagnosis/status/" in mail.sent[0]["html"]
        assert "발송테스트의원" in mail.sent[0]["subject"]

    async def test_required_notices_are_in_the_email(self, pg_async_session, mail):
        """F5-5 고지 2종은 리포트에만이 아니라 전달 매체에도 있어야 한다."""
        diagnosis = await _seed(pg_async_session)
        await lead_delivery.deliver_report(pg_async_session, diagnosis)
        html = mail.sent[0]["html"]
        assert "광고물이 아닙니다" in html
        assert "인공지능" in html


@pytest.mark.asyncio
class TestNoDoubleSend:
    async def test_second_call_after_success_does_not_send_again(self, pg_async_session, mail):
        diagnosis = await _seed(pg_async_session)
        await lead_delivery.deliver_report(pg_async_session, diagnosis)
        result = await lead_delivery.deliver_report(pg_async_session, diagnosis)

        assert result == {"skipped": "already_sent"}
        assert len(mail.sent) == 1

    async def test_retry_after_a_failure_reuses_the_same_key(self, pg_async_session, monkeypatch):
        """T-8 — attempt가 올라가도 키가 바뀌면 재시도가 곧 두 번째 메일이 된다."""
        spy = _MailSpy(fail_times=1)
        monkeypatch.setattr(mailer, "send_email", spy.send_email)
        monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")

        diagnosis = await _seed(pg_async_session)
        with pytest.raises(RuntimeError):
            await lead_delivery.deliver_report(pg_async_session, diagnosis)
        await lead_delivery.deliver_report(pg_async_session, diagnosis)

        assert len(spy.keys) == 2
        assert spy.keys[0] == spy.keys[1]

    async def test_retry_payload_is_byte_identical(self, pg_async_session, monkeypatch):
        """Resend는 같은 키에 다른 payload가 오면 409를 준다 — 본문이 결정적이어야 한다."""
        spy = _MailSpy(fail_times=1)
        monkeypatch.setattr(mailer, "send_email", spy.send_email)
        monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")

        diagnosis = await _seed(pg_async_session)
        with pytest.raises(RuntimeError):
            await lead_delivery.deliver_report(pg_async_session, diagnosis)
        await lead_delivery.deliver_report(pg_async_session, diagnosis)

        assert spy.sent[0]["html"] == spy.sent[1]["html"]
        assert spy.sent[0]["subject"] == spy.sent[1]["subject"]

    async def test_only_one_delivery_row_per_diagnosis(self, pg_async_session, monkeypatch):
        spy = _MailSpy(fail_times=2)
        monkeypatch.setattr(mailer, "send_email", spy.send_email)
        monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")

        diagnosis = await _seed(pg_async_session)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await lead_delivery.deliver_report(pg_async_session, diagnosis)

        rows = (
            await pg_async_session.execute(
                select(LeadDelivery).where(LeadDelivery.diagnosis_id == diagnosis.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].attempt == 2


@pytest.mark.asyncio
class TestGates:
    async def test_report_not_ready_is_not_sent(self, pg_async_session, mail):
        """리포트 없이 메일이 나가면 원장이 죽은 링크를 받는다."""
        diagnosis = await _seed(pg_async_session, report_status=ReportStatus.PENDING.value)
        result = await lead_delivery.deliver_report(pg_async_session, diagnosis)
        assert result == {"skipped": "report_not_ready"}
        assert mail.sent == []

    async def test_purged_lead_is_not_emailed(self, pg_async_session, mail):
        """파기 후 재발송 시도는 정상 경로다 — 실패로 기록하지 않는다."""
        diagnosis = await _seed(pg_async_session, email="[purged]")
        result = await lead_delivery.deliver_report(pg_async_session, diagnosis)
        assert result == {"skipped": "no_recipient"}
        assert mail.sent == []

    async def test_unconfigured_mailer_raises_instead_of_pretending(self, pg_async_session,
                                                                    monkeypatch):
        """조용히 성공 처리하면 '발송 완료'인데 아무도 못 받는 상태가 된다."""
        monkeypatch.setattr(settings, "RESEND_API_KEY", "")
        diagnosis = await _seed(pg_async_session)
        with pytest.raises(mailer.MailNotConfigured):
            await lead_delivery.deliver_report(pg_async_session, diagnosis)


@pytest.mark.asyncio
class TestStuckSweep:
    async def _stuck(self, session, *, age, attempt=1):
        diagnosis = await _seed(session, delivery_status=DeliveryStatus.SENDING.value)
        delivery = LeadDelivery(
            id=uuid.uuid4(),
            lead_id=diagnosis.lead_id,
            diagnosis_id=diagnosis.id,
            channel="EMAIL",
            event="REPORT",
            status=DeliveryStatus.SENDING.value,
            attempt=attempt,
        )
        session.add(delivery)
        await session.flush()
        delivery.created_at = datetime.now(timezone.utc) - age
        await session.flush()
        return diagnosis, delivery

    async def test_a_recently_stuck_row_is_not_retried_yet(self, pg_async_session):
        """즉시 재시도하면 실패 원인이 그대로 남아 있는 상태에서 다시 던지게 된다."""
        _, delivery = await self._stuck(pg_async_session, age=timedelta(minutes=1))
        result = await lead_delivery.sweep_stuck_deliveries(pg_async_session)
        assert str(delivery.diagnosis_id) not in result["retriable"]

    async def test_a_due_row_is_retried(self, pg_async_session):
        diagnosis, _ = await self._stuck(pg_async_session, age=timedelta(minutes=10))
        result = await lead_delivery.sweep_stuck_deliveries(pg_async_session)
        assert str(diagnosis.id) in result["retriable"]

    async def test_rows_past_the_idempotency_window_are_abandoned(self, pg_async_session):
        """T-9 — Resend는 키를 24시간만 보관한다. 그 밖의 자동 재시도는 중복 발송이다."""
        diagnosis, delivery = await self._stuck(pg_async_session, age=timedelta(hours=25))
        result = await lead_delivery.sweep_stuck_deliveries(pg_async_session)

        assert str(delivery.id) in result["abandoned"]
        assert str(diagnosis.id) not in result["retriable"]
        await pg_async_session.refresh(delivery)
        await pg_async_session.refresh(diagnosis)
        assert delivery.status == DeliveryStatus.FAILED.value
        assert "24시간" in (delivery.error or "")
        assert diagnosis.delivery_status == DeliveryStatus.FAILED.value

    async def test_exhausted_attempts_are_abandoned(self, pg_async_session):
        diagnosis, delivery = await self._stuck(
            pg_async_session, age=timedelta(hours=5), attempt=lead_delivery.MAX_ATTEMPTS
        )
        result = await lead_delivery.sweep_stuck_deliveries(pg_async_session)
        assert str(delivery.id) in result["abandoned"]
        await pg_async_session.refresh(delivery)
        assert delivery.status == DeliveryStatus.FAILED.value

@pytest.mark.asyncio
class TestManualRearm:
    """수동 재발송 — **없으면 발송 실패가 리드 영구 소실이다.**

    자동 재시도 4회가 소진되면 FAILED가 종결이고 리포트는 만들어져 있는데 보낼 방법이
    없다. 전화번호·이메일이 영구 잠금이라 신청자가 다시 신청할 수도 없다.
    """

    async def _failed(self, session, *, age, attempt=None, status=None):
        diagnosis = await _seed(session, delivery_status=DeliveryStatus.FAILED.value)
        delivery = LeadDelivery(
            id=uuid.uuid4(),
            lead_id=diagnosis.lead_id,
            diagnosis_id=diagnosis.id,
            channel="EMAIL",
            event="REPORT",
            status=status or DeliveryStatus.FAILED.value,
            attempt=attempt if attempt is not None else lead_delivery.MAX_ATTEMPTS,
            error="RuntimeError: resend_error_401",
        )
        session.add(delivery)
        await session.flush()
        delivery.created_at = datetime.now(timezone.utc) - age
        await session.flush()
        return diagnosis, delivery

    async def test_a_failed_delivery_inside_the_window_is_rearmed(self, pg_async_session):
        diagnosis, delivery = await self._failed(pg_async_session, age=timedelta(hours=5))
        result = await lead_delivery.rearm_report_delivery(
            pg_async_session, diagnosis, actor="ae@motionlabs.kr", reason="Resend 키 교체"
        )

        assert result["ok"] is True
        assert diagnosis.delivery_status == DeliveryStatus.PENDING.value
        assert delivery.status == DeliveryStatus.PENDING.value
        # 폴러가 집어 deliver_report가 1회차로 다시 보낼 수 있는 상태여야 한다.
        assert delivery.attempt == 0

    async def test_rearm_keeps_the_idempotency_key(self, pg_async_session):
        """행 id가 곧 Resend 키다 — 바꾸면 창 안 재발송이 두 번째 메일이 된다."""
        diagnosis, delivery = await self._failed(pg_async_session, age=timedelta(hours=5))
        original_id = delivery.id
        await lead_delivery.rearm_report_delivery(
            pg_async_session, diagnosis, actor="ae", reason="재발송"
        )
        assert delivery.id == original_id

    async def test_rearm_then_send_actually_delivers_with_the_same_key(
        self, pg_async_session, mail
    ):
        """되살린 뒤 실제로 발송까지 되는지 — 컬럼만 뒤집고 끝나면 복구가 아니다."""
        diagnosis, delivery = await self._failed(pg_async_session, age=timedelta(hours=5))
        await lead_delivery.rearm_report_delivery(
            pg_async_session, diagnosis, actor="ae", reason="재발송"
        )
        result = await lead_delivery.deliver_report(pg_async_session, diagnosis)

        assert len(mail.sent) == 1
        assert mail.keys == [str(delivery.id)]
        assert diagnosis.delivery_status == DeliveryStatus.SENT.value
        assert result["attempt"] == 1

    async def test_rearm_does_not_create_a_second_delivery_row(self, pg_async_session, mail):
        """(diagnosis, event, channel) 유니크가 있고 `_existing_delivery`는 단일 행을 기대한다 —
        행을 새로 만들면 이후 모든 발송이 MultipleResultsFound로 죽는다."""
        diagnosis, _ = await self._failed(pg_async_session, age=timedelta(hours=5))
        await lead_delivery.rearm_report_delivery(
            pg_async_session, diagnosis, actor="ae", reason="재발송"
        )
        await lead_delivery.deliver_report(pg_async_session, diagnosis)

        rows = (
            await pg_async_session.execute(
                select(LeadDelivery).where(LeadDelivery.diagnosis_id == diagnosis.id)
            )
        ).scalars().all()
        assert len(rows) == 1

    async def test_past_the_window_it_refuses_without_acknowledgement(self, pg_async_session):
        """24시간이 지나면 원래 메일이 나갔는지 알 수 없다 — 사람이 판단해야 한다."""
        diagnosis, _ = await self._failed(pg_async_session, age=timedelta(hours=30))
        result = await lead_delivery.rearm_report_delivery(
            pg_async_session, diagnosis, actor="ae", reason="재발송"
        )
        assert result["ok"] is False
        assert result["code"] == "duplicate_risk_unacknowledged"
        assert diagnosis.delivery_status == DeliveryStatus.FAILED.value

    async def test_past_the_window_with_acknowledgement_is_rearmed_and_sendable(
        self, pg_async_session, mail
    ):
        """동의하면 되살린다. created_at까지 리셋해야 deliver_report가 즉시 창 밖으로
        판정하지 않는다 — 리셋을 빼면 '되살렸는데 안 나가는' 상태가 된다."""
        diagnosis, _ = await self._failed(pg_async_session, age=timedelta(hours=30))
        result = await lead_delivery.rearm_report_delivery(
            pg_async_session,
            diagnosis,
            actor="ae",
            reason="원장 재요청",
            acknowledge_duplicate_risk=True,
        )
        assert result["ok"] is True
        assert result["duplicate_risk_acknowledged"] is True

        send = await lead_delivery.deliver_report(pg_async_session, diagnosis)
        assert send.get("skipped") is None
        assert len(mail.sent) == 1

    async def test_already_sent_is_refused(self, pg_async_session):
        diagnosis, _ = await self._failed(
            pg_async_session, age=timedelta(hours=5), status=DeliveryStatus.SENT.value
        )
        result = await lead_delivery.rearm_report_delivery(
            pg_async_session, diagnosis, actor="ae", reason="재발송"
        )
        assert result["code"] == "already_sent"

    async def test_in_flight_delivery_is_left_to_the_sweep(self, pg_async_session):
        """자동 재시도 중에 끼어들면 attempt만 태우고 발송 기회를 줄인다."""
        diagnosis, _ = await self._failed(
            pg_async_session,
            age=timedelta(minutes=10),
            attempt=2,
            status=DeliveryStatus.SENDING.value,
        )
        result = await lead_delivery.rearm_report_delivery(
            pg_async_session, diagnosis, actor="ae", reason="재발송"
        )
        assert result["code"] == "in_flight"

    async def test_a_stuck_sending_row_past_the_window_can_be_rearmed_with_ack(
        self, pg_async_session
    ):
        """스윕이 FAILED로 바꾸기 전이라도 창을 넘긴 SENDING은 사람이 처리할 수 있어야 한다."""
        diagnosis, _ = await self._failed(
            pg_async_session,
            age=timedelta(hours=30),
            attempt=2,
            status=DeliveryStatus.SENDING.value,
        )
        result = await lead_delivery.rearm_report_delivery(
            pg_async_session,
            diagnosis,
            actor="ae",
            reason="확인 후 재발송",
            acknowledge_duplicate_risk=True,
        )
        assert result["ok"] is True

    async def test_report_not_ready_is_refused(self, pg_async_session):
        diagnosis = await _seed(pg_async_session, report_status=ReportStatus.BLOCKED.value)
        result = await lead_delivery.rearm_report_delivery(
            pg_async_session, diagnosis, actor="ae", reason="재발송"
        )
        assert result["code"] == "report_not_ready"

    async def test_purged_lead_is_refused(self, pg_async_session):
        diagnosis = await _seed(pg_async_session, email="[purged]")
        result = await lead_delivery.rearm_report_delivery(
            pg_async_session, diagnosis, actor="ae", reason="재발송"
        )
        assert result["code"] == "no_recipient"

    async def test_a_diagnosis_with_no_delivery_row_is_simply_requeued(self, pg_async_session):
        """행이 없으면 나간 메일도 없다 — 폴러가 새로 만들게 두면 된다."""
        diagnosis = await _seed(pg_async_session, delivery_status=DeliveryStatus.FAILED.value)
        result = await lead_delivery.rearm_report_delivery(
            pg_async_session, diagnosis, actor="ae", reason="재발송"
        )
        assert result["ok"] is True
        assert result["delivery_id"] is None
        assert diagnosis.delivery_status == DeliveryStatus.PENDING.value


@pytest.mark.asyncio
class TestRetryDeliveryEndpoint:
    """AE가 실제로 누르는 표면. 서비스가 거절한 이유가 응답에 그대로 실려야 한다 —
    운영자가 보는 설명이 그것뿐이다."""

    async def _failed_lead(self, session, *, age=timedelta(hours=5)):
        diagnosis = await _seed(session, delivery_status=DeliveryStatus.FAILED.value)
        delivery = LeadDelivery(
            id=uuid.uuid4(),
            lead_id=diagnosis.lead_id,
            diagnosis_id=diagnosis.id,
            channel="EMAIL",
            event="REPORT",
            status=DeliveryStatus.FAILED.value,
            attempt=lead_delivery.MAX_ATTEMPTS,
        )
        session.add(delivery)
        await session.flush()
        delivery.created_at = datetime.now(timezone.utc) - age
        await session.flush()
        return diagnosis

    async def test_unknown_lead_is_404(self, pg_async_session):
        with pytest.raises(HTTPException) as exc:
            await leads_api.retry_report_delivery(
                uuid.uuid4(),
                leads_api.RetryDeliveryRequest(reason="테스트"),
                db=pg_async_session,
            )
        assert exc.value.status_code == 404

    async def test_success_records_an_audit_log(self, pg_async_session):
        diagnosis = await self._failed_lead(pg_async_session)
        response = await leads_api.retry_report_delivery(
            diagnosis.lead_id,
            leads_api.RetryDeliveryRequest(reason="Resend 키 교체 후 재발송"),
            db=pg_async_session,
        )
        assert response["rearmed_count"] == 1

        logs = (
            await pg_async_session.execute(
                select(AdminAuditLog).where(
                    AdminAuditLog.action == "retry_lead_report_delivery",
                    AdminAuditLog.target_id == str(diagnosis.lead_id),
                )
            )
        ).scalars().all()
        assert len(logs) == 1
        assert logs[0].detail["reason"] == "Resend 키 교체 후 재발송"

    async def test_refusal_explains_why_per_diagnosis(self, pg_async_session):
        """24시간을 넘긴 건은 409로 막되, 무엇을 확인해야 하는지 알려줘야 한다."""
        diagnosis = await self._failed_lead(pg_async_session, age=timedelta(hours=30))
        with pytest.raises(HTTPException) as exc:
            await leads_api.retry_report_delivery(
                diagnosis.lead_id,
                leads_api.RetryDeliveryRequest(reason="재발송"),
                db=pg_async_session,
            )
        assert exc.value.status_code == 409
        codes = [d["code"] for d in exc.value.detail["diagnoses"]]
        assert codes == ["duplicate_risk_unacknowledged"]

    async def test_refusal_writes_no_audit_trail(self, pg_async_session):
        """거절은 아무것도 하지 않은 것이어야 한다 — 감사 로그가 남으면 '재발송했다'는
        기록만 남고 메일은 안 나간 상태가 된다.

        (거절이 진단 상태를 건드리지 않는다는 검증은 서비스 계층에 있다 —
        `TestManualRearm.test_past_the_window_it_refuses_without_acknowledgement`.)
        """
        diagnosis = await self._failed_lead(pg_async_session, age=timedelta(hours=30))
        lead_id = diagnosis.lead_id
        with pytest.raises(HTTPException):
            await leads_api.retry_report_delivery(
                lead_id,
                leads_api.RetryDeliveryRequest(reason="재발송"),
                db=pg_async_session,
            )
        logs = (
            await pg_async_session.execute(
                select(AdminAuditLog).where(
                    AdminAuditLog.action == "retry_lead_report_delivery"
                )
            )
        ).scalars().all()
        assert logs == []

    async def test_lead_without_a_diagnosis_is_409(self, pg_async_session):
        lead = SalesLead(
            clinic_name="진단없는의원",
            clinic_type="내과",
            contact="010-0000-0002",
            email="none@example.com",
            privacy=True,
            source="AI_DIAGNOSIS",
        )
        pg_async_session.add(lead)
        await pg_async_session.flush()
        with pytest.raises(HTTPException) as exc:
            await leads_api.retry_report_delivery(
                lead.id,
                leads_api.RetryDeliveryRequest(reason="재발송"),
                db=pg_async_session,
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
class TestRetryLadderInvariants:
    async def test_all_retry_delays_fit_inside_the_idempotency_window(self):
        """재시도 일정이 24시간을 넘으면 마지막 재시도가 중복 발송이 된다.

        상수 하나만 늘려도 조용히 깨지는 관계라, 값이 아니라 **관계**를 고정한다.
        """
        assert max(lead_delivery.RETRY_DELAYS) < lead_delivery.IDEMPOTENCY_WINDOW
        assert sum(lead_delivery.RETRY_DELAYS, timedelta()) < lead_delivery.IDEMPOTENCY_WINDOW
