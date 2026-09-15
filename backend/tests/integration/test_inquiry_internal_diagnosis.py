"""INQUIRY reports are generated for Admin use without entering customer delivery."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select

from app.api.admin import leads as leads_api
from app.api.public import diagnosis as diagnosis_api
from app.models.admin_user import AdminUser
from app.models.lead import SalesLead, is_internal_inquiry
from app.models.lead_diagnosis import (
    DeliveryStatus,
    ExecutionStatus,
    LeadDelivery,
    LeadDiagnosis,
    LeadDiagnosisSlotDay,
    LeadReportToken,
    ReportStatus,
)
from app.services import lead_delivery, mailer
from app.workers.lead_diagnosis_tasks import _deliveries_to_send


def _actor() -> AdminUser:
    return AdminUser(
        id=uuid.uuid4(),
        email="ae@motionlabs.kr",
        name="AE",
        role="OPERATOR",
        password_hash="not-used",
        is_active=True,
    )


async def _inquiry(session, *, source="INQUIRY", clinic_type="도입문의") -> SalesLead:
    lead = SalesLead(
        clinic_name=f"내부진단{uuid.uuid4().hex[:6]}의원",
        clinic_type=clinic_type,
        contact="010-1234-5678",
        question="병원 주소: 서울시 강남구\n원장님 성함: 김원장",
        privacy=True,
        source=source,
    )
    session.add(lead)
    await session.flush()
    return lead


def _request() -> leads_api.InternalDiagnosisRequest:
    return leads_api.InternalDiagnosisRequest(
        email="Director+sales@Example.com",
        clinic_type="정형외과",
        region_keyword="강남역",
        core_keywords=["도수치료", "허리통증"],
        clinic_phone="02-123-4567",
        contact_name="김원장",
    )


@pytest.mark.asyncio
class TestInternalDiagnosisCreation:
    async def test_admin_creation_skips_free_quota_locks_and_public_token(
        self, pg_async_session, monkeypatch
    ):
        lead = await _inquiry(pg_async_session)
        slots_before = int(
            await pg_async_session.scalar(select(func.sum(LeadDiagnosisSlotDay.used))) or 0
        )
        queued: list[str] = []
        monkeypatch.setattr(
            leads_api,
            "_enqueue_internal_diagnosis",
            lambda diagnosis_id: queued.append(diagnosis_id),
        )
        background_tasks = BackgroundTasks()

        result = await leads_api.create_internal_inquiry_diagnosis(
            lead.id,
            _request(),
            background_tasks,
            db=pg_async_session,
            actor=_actor(),
        )
        await background_tasks()

        diagnosis = await pg_async_session.scalar(
            select(LeadDiagnosis).where(LeadDiagnosis.id == uuid.UUID(result["diagnosis_id"]))
        )
        assert diagnosis is not None
        assert diagnosis.delivery_status == DeliveryStatus.INTERNAL.value
        assert diagnosis.execution_status == ExecutionStatus.PENDING.value
        assert diagnosis.report_status == ReportStatus.PENDING.value
        assert diagnosis.applicant_email_hash is None
        assert diagnosis.subject_phone_hash is None
        assert diagnosis.slot_date is None
        assert diagnosis.slot_no is None
        assert queued == [str(diagnosis.id)]

        slots_after = int(
            await pg_async_session.scalar(select(func.sum(LeadDiagnosisSlotDay.used))) or 0
        )
        assert slots_after == slots_before
        assert await pg_async_session.scalar(
            select(func.count()).select_from(LeadReportToken).where(
                LeadReportToken.diagnosis_id == diagnosis.id
            )
        ) == 0
        assert await pg_async_session.scalar(
            select(func.count()).select_from(LeadDelivery).where(
                LeadDelivery.diagnosis_id == diagnosis.id
            )
        ) == 0

        await pg_async_session.refresh(lead)
        assert lead.source == "INQUIRY"
        assert lead.email == "director@example.com"
        assert lead.region_keyword == "강남역"
        assert lead.core_keywords == ["도수치료", "허리통증"]

    async def test_creation_preserves_the_introduction_inquiry_marker(
        self, pg_async_session, monkeypatch
    ):
        """진료과 입력이 도입문의 표식을 덮으면 그 리드는 일반 진단 신청처럼 보인다.

        clinic_type 하나만 보는 판정(Admin 목록 배지·도입문의 상세 카드, 고객 발송
        폴러의 레거시 방어선)이 전부 이 표식에 걸려 있다.
        """
        lead = await _inquiry(pg_async_session)
        monkeypatch.setattr(leads_api, "_enqueue_internal_diagnosis", lambda diagnosis_id: None)

        result = await leads_api.create_internal_inquiry_diagnosis(
            lead.id,
            _request(),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )

        await pg_async_session.refresh(lead)
        assert lead.clinic_type == "도입문의"
        assert is_internal_inquiry(lead) is True
        assert leads_api._serialize_lead(lead)["clinic_type"] == "도입문의"

        # 진료과는 사라지지 않는다 — 슬롯 1 진료과 앵커가 그대로 들고 있다.
        diagnosis = await pg_async_session.scalar(
            select(LeadDiagnosis).where(LeadDiagnosis.id == uuid.UUID(result["diagnosis_id"]))
        )
        assert any("정형외과" in query["text"] for query in diagnosis.queries)

    async def test_creation_still_enriches_a_lead_without_the_marker(
        self, pg_async_session, monkeypatch
    ):
        """표식이 없는 INQUIRY 리드는 자유 텍스트 대신 AE가 고른 진료과를 받는다."""
        lead = await _inquiry(pg_async_session, clinic_type="강남 대장항문외과")
        monkeypatch.setattr(leads_api, "_enqueue_internal_diagnosis", lambda diagnosis_id: None)

        await leads_api.create_internal_inquiry_diagnosis(
            lead.id,
            _request(),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )

        await pg_async_session.refresh(lead)
        assert lead.clinic_type == "정형외과"
        assert is_internal_inquiry(lead) is True

    async def test_non_inquiry_lead_is_refused(self, pg_async_session):
        lead = await _inquiry(
            pg_async_session, source="AI_DIAGNOSIS", clinic_type="정형외과"
        )
        with pytest.raises(HTTPException) as exc:
            await leads_api.create_internal_inquiry_diagnosis(
                lead.id,
                _request(),
                BackgroundTasks(),
                db=pg_async_session,
                actor=_actor(),
            )
        assert exc.value.status_code == 400

    async def test_second_internal_diagnosis_is_refused(self, pg_async_session):
        lead = await _inquiry(pg_async_session)
        await leads_api.create_internal_inquiry_diagnosis(
            lead.id,
            _request(),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )
        with pytest.raises(HTTPException) as exc:
            await leads_api.create_internal_inquiry_diagnosis(
                lead.id,
                _request(),
                BackgroundTasks(),
                db=pg_async_session,
                actor=_actor(),
            )
        assert exc.value.status_code == 409


async def _legacy_pending_inquiry(session, *, source="INQUIRY", clinic_type="정형외과"):
    lead = await _inquiry(session, source=source, clinic_type=clinic_type)
    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        applicant_email_hash=uuid.uuid4().hex,
        subject_phone_hash=uuid.uuid4().hex,
        subject_hospital_name=lead.clinic_name,
        subject_region="강남역",
        slot_date=date(2026, 9, 14),
        slot_no=int(uuid.uuid4().int % 1_000_000) + 10_000,
        queries=[{"slot": 1, "kind": "진료과형", "text": "강남역 정형외과 추천"}],
        requested_models={"openai": "m", "gemini": "g", "judge": "j"},
        repeat_count=1,
        execution_status=ExecutionStatus.SUCCEEDED.value,
        report_status=ReportStatus.READY.value,
        delivery_status=DeliveryStatus.PENDING.value,
    )
    session.add(diagnosis)
    await session.flush()
    return lead, diagnosis


@pytest.mark.asyncio
class TestCustomerDeliveryFences:
    async def test_drain_excludes_inquiry_source_and_legacy_clinic_type(self, pg_async_session):
        _, by_source = await _legacy_pending_inquiry(pg_async_session)
        _, by_type = await _legacy_pending_inquiry(
            pg_async_session, source="AI_DIAGNOSIS", clinic_type="도입문의"
        )

        selected = await _deliveries_to_send(pg_async_session)

        assert str(by_source.id) not in selected
        assert str(by_type.id) not in selected

    async def test_direct_delivery_holds_inquiry_without_calling_resend(
        self, pg_async_session, monkeypatch
    ):
        _, diagnosis = await _legacy_pending_inquiry(pg_async_session)
        called = False

        async def forbidden_send(**kwargs):
            nonlocal called
            called = True
            raise AssertionError("Resend must not be called for INQUIRY")

        monkeypatch.setattr(mailer, "send_email", forbidden_send)
        result = await lead_delivery.deliver_report(pg_async_session, diagnosis)

        assert result == {"skipped": "internal_only"}
        assert called is False
        assert diagnosis.delivery_status == DeliveryStatus.INTERNAL.value
        assert await pg_async_session.scalar(
            select(func.count()).select_from(LeadDelivery).where(
                LeadDelivery.diagnosis_id == diagnosis.id
            )
        ) == 0

    async def test_stuck_inquiry_is_held_and_never_returned_for_retry(self, pg_async_session):
        lead, diagnosis = await _legacy_pending_inquiry(pg_async_session)
        diagnosis.delivery_status = DeliveryStatus.SENDING.value
        delivery = LeadDelivery(
            lead_id=lead.id,
            diagnosis_id=diagnosis.id,
            channel="EMAIL",
            event="REPORT",
            status=DeliveryStatus.SENDING.value,
            attempt=1,
        )
        pg_async_session.add(delivery)
        await pg_async_session.flush()
        delivery.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        result = await lead_delivery.sweep_stuck_deliveries(pg_async_session)

        assert str(delivery.id) in result["internal"]
        assert str(diagnosis.id) not in result["retriable"]
        assert delivery.status == DeliveryStatus.INTERNAL.value
        assert diagnosis.delivery_status == DeliveryStatus.INTERNAL.value

    async def test_admin_retry_is_forbidden_for_inquiry(self, pg_async_session):
        lead, _ = await _legacy_pending_inquiry(pg_async_session)
        with pytest.raises(HTTPException) as exc:
            await leads_api.retry_report_delivery(
                lead.id,
                leads_api.RetryDeliveryRequest(reason="고객 요청 아님"),
                db=pg_async_session,
            )
        assert exc.value.status_code == 403

    async def test_public_token_is_rejected_even_if_a_legacy_row_exists(self, pg_async_session):
        _, diagnosis = await _legacy_pending_inquiry(pg_async_session)
        raw_token = "legacy-inquiry-token"
        token = LeadReportToken(
            diagnosis_id=diagnosis.id,
            token_hash=diagnosis_api.lead_report_token.hash_report_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        pg_async_session.add(token)
        await pg_async_session.flush()

        with pytest.raises(HTTPException) as exc:
            await diagnosis_api._resolve_token(pg_async_session, raw_token)
        assert exc.value.status_code == 404


# ── 공개 접수의 자동 생성은 Admin 경로와 같은 행을 만든다 ─────────────────────────

from types import SimpleNamespace  # noqa: E402

from app.api.public import leads as public_leads_api  # noqa: E402
from app.models.audit import AdminAuditLog  # noqa: E402
from app.services import inquiry_diagnosis, inquiry_sms, notifier  # noqa: E402


class _FakeRequest:
    def __init__(self):
        self.headers = SimpleNamespace(get=lambda key, default=None: default)
        self.client = SimpleNamespace(host="127.0.0.1")


def _public_body(**overrides) -> public_leads_api.LeadCreate:
    fields = dict(
        clinic_name=f"자동진단{uuid.uuid4().hex[:6]}의원",
        clinic_type="도입문의",
        contact=f"010-{uuid.uuid4().int % 9000 + 1000}-{uuid.uuid4().int % 9000 + 1000}",
        question="병원 주소: 서울 강남구\n원장님 성함: 김원장\n병원 홈페이지: https://x.example",
        privacy=True,
        source_path="/contact",
        specialty="정형외과",
        region_keyword="강남역",
        core_keywords=["도수치료", "허리통증"],
        contact_name="김원장",
    )
    fields.update(overrides)
    return public_leads_api.LeadCreate(**fields)


@pytest.mark.asyncio
class TestPublicIntakeAutoDiagnosis:
    async def test_intake_creates_a_queued_internal_diagnosis(self, pg_async_session, monkeypatch):
        async def fake_notify(**payload):
            return True

        monkeypatch.setattr(notifier, "notify_lead_created", fake_notify)
        monkeypatch.setattr(inquiry_sms.settings, "INQUIRY_SMS_PROVIDER", "")
        queued: list[str] = []
        monkeypatch.setattr(inquiry_diagnosis, "enqueue_inquiry_diagnosis", queued.append)

        background_tasks = BackgroundTasks()
        response = await public_leads_api.create_lead.__wrapped__(
            request=_FakeRequest(),
            body=_public_body(),
            background_tasks=background_tasks,
            db=pg_async_session,
        )
        await background_tasks()

        assert response["diagnosis_id"] is not None
        assert queued == [response["diagnosis_id"]]
        diagnosis = await pg_async_session.scalar(
            select(LeadDiagnosis).where(LeadDiagnosis.id == uuid.UUID(response["diagnosis_id"]))
        )
        assert diagnosis.delivery_status == DeliveryStatus.INTERNAL.value
        assert diagnosis.execution_status == ExecutionStatus.PENDING.value
        assert diagnosis.slot_date is None and diagnosis.applicant_email_hash is None
        assert any("정형외과" in query["text"] for query in diagnosis.queries)

        lead = await pg_async_session.scalar(
            select(SalesLead).where(SalesLead.id == uuid.UUID(response["lead_id"]))
        )
        assert lead.clinic_type == "도입문의"
        assert lead.specialty == "정형외과"
        assert lead.core_keywords == ["도수치료", "허리통증"]
        assert lead.ack_sms_status == "SKIPPED"
        assert is_internal_inquiry(lead) is True

        audit = await pg_async_session.scalar(
            select(AdminAuditLog).where(AdminAuditLog.target_id == str(diagnosis.id))
        )
        assert audit is not None
        assert audit.actor == "system:public-inquiry"

        # Admin은 같은 리드에 두 번째 진단을 만들 수 없다 — 규칙이 두 경로에서 같다.
        with pytest.raises(HTTPException) as exc:
            await leads_api.create_internal_inquiry_diagnosis(
                lead.id, _request(), BackgroundTasks(), db=pg_async_session, actor=_actor()
            )
        assert exc.value.status_code == 409

    async def test_refused_input_still_records_the_lead_and_leaves_no_diagnosis(
        self, pg_async_session, monkeypatch
    ):
        async def fake_notify(**payload):
            return True

        monkeypatch.setattr(notifier, "notify_lead_created", fake_notify)
        monkeypatch.setattr(inquiry_sms.settings, "INQUIRY_SMS_PROVIDER", "")
        body = _public_body(clinic_name="강남역정형외과의원", core_keywords=["강남역 정형외과 의원"])

        response = await public_leads_api.create_lead.__wrapped__(
            request=_FakeRequest(), body=body, background_tasks=BackgroundTasks(), db=pg_async_session
        )

        assert response["ok"] is True
        assert response["diagnosis_id"] is None
        lead = await pg_async_session.scalar(
            select(SalesLead).where(SalesLead.id == uuid.UUID(response["lead_id"]))
        )
        assert lead is not None
        assert await pg_async_session.scalar(
            select(func.count()).select_from(LeadDiagnosis).where(LeadDiagnosis.lead_id == lead.id)
        ) == 0

    async def test_same_contact_gets_one_acknowledgement_sms(self, pg_async_session, monkeypatch):
        async def fake_notify(**payload):
            return True

        sent: list[str] = []

        async def fake_deliver(to):
            sent.append(to)
            return inquiry_sms.AckSmsOutcome("SENT", None, "req")

        monkeypatch.setattr(notifier, "notify_lead_created", fake_notify)
        monkeypatch.setattr(inquiry_diagnosis, "enqueue_inquiry_diagnosis", lambda _id: None)
        monkeypatch.setattr(inquiry_sms, "deliver", fake_deliver)
        for name, value in {
            "INQUIRY_SMS_PROVIDER": "nhn",
            "NHN_SMS_APP_KEY": "k",
            "NHN_SMS_SECRET_KEY": "s",
            "INQUIRY_SMS_SENDER_NO": "010-2492-8543",
        }.items():
            monkeypatch.setattr(inquiry_sms.settings, name, value)

        contact = "010-5555-1234"
        first = await public_leads_api.create_lead.__wrapped__(
            request=_FakeRequest(),
            body=_public_body(contact=contact),
            background_tasks=BackgroundTasks(),
            db=pg_async_session,
        )
        second = await public_leads_api.create_lead.__wrapped__(
            request=_FakeRequest(),
            body=_public_body(contact=contact),
            background_tasks=BackgroundTasks(),
            db=pg_async_session,
        )

        assert first["ack_sms"] == "sent"
        assert second["ack_sms"] == "skipped"
        assert sent == ["01055551234"]
