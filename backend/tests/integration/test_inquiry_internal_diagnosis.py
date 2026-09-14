"""INQUIRY reports are generated for Admin use without entering customer delivery."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select

from app.api.admin import leads as leads_api
from app.api.public import diagnosis as diagnosis_api
from app.models.admin_user import AdminUser
from app.models.lead import SalesLead
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
        assert lead.clinic_type == "정형외과"
        assert lead.region_keyword == "강남역"
        assert lead.core_keywords == ["도수치료", "허리통증"]

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
