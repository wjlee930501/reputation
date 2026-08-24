"""Admin 리드 목록의 무료 진단 요약 (운영 화면의 데이터 계약).

**이 요약이 없으면 AE는 신청 1건도 콘솔에서 볼 수 없다.** 실패는 Slack으로만 알려졌고,
그 알림을 놓치면 되짚을 표면이 없었다.
"""
import itertools
import uuid
from datetime import date

import pytest

from app.api.admin import leads as leads_api
from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    DeliveryStatus,
    ExecutionStatus,
    LeadDiagnosis,
    ReportStatus,
)

_slot_sequence = itertools.count(900)


async def _seed_lead(
    session,
    *,
    clinic_name,
    execution_status=ExecutionStatus.SUCCEEDED.value,
    report_status=ReportStatus.READY.value,
    delivery_status=DeliveryStatus.SENT.value,
    with_diagnosis=True,
):
    lead = SalesLead(
        clinic_name=clinic_name,
        clinic_type="내과",
        contact="010-0000-0000",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        privacy=True,
        source="AI_DIAGNOSIS",
    )
    session.add(lead)
    await session.flush()
    if not with_diagnosis:
        return lead, None

    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        applicant_email_hash=uuid.uuid4().hex,
        subject_phone_hash=uuid.uuid4().hex,
        subject_hospital_name=clinic_name,
        subject_region="수서역",
        slot_date=date(2026, 8, 20),
        slot_no=next(_slot_sequence),
        queries=[{"slot": 1, "kind": "진료과형", "text": "수서역 내과 추천"}],
        requested_models={"openai": "m", "gemini": "g", "judge": "j"},
        repeat_count=3,
        execution_status=execution_status,
        report_status=report_status,
        delivery_status=delivery_status,
    )
    session.add(diagnosis)
    await session.flush()
    return lead, diagnosis


def _find(rows, lead_id):
    return next((row for row in rows if row["id"] == str(lead_id)), None)


@pytest.mark.asyncio
class TestDiagnosisSummary:
    async def test_the_three_axes_are_all_exposed(self, pg_async_session):
        """단일 상태로 접으면 '측정 일부 실패 + 발송 완료'가 화면에서 사라진다."""
        lead, _ = await _seed_lead(
            pg_async_session,
            clinic_name="3축의원",
            execution_status=ExecutionStatus.PARTIAL.value,
        )
        rows = await leads_api.list_sales_leads(db=pg_async_session, limit=200, offset=0)
        summary = _find(rows, lead.id)["diagnoses"][0]

        assert summary["execution_status"] == ExecutionStatus.PARTIAL.value
        assert summary["report_status"] == ReportStatus.READY.value
        assert summary["delivery_status"] == DeliveryStatus.SENT.value

    async def test_summary_carries_no_extra_identifying_fields(self, pg_async_session):
        """이 목록은 이미 대량 PII 열람이다 — 진단 요약에 병원명·지역을 또 얹지 않는다."""
        lead, _ = await _seed_lead(pg_async_session, clinic_name="식별정보의원")
        rows = await leads_api.list_sales_leads(db=pg_async_session, limit=200, offset=0)
        summary = _find(rows, lead.id)["diagnoses"][0]

        assert "subject_hospital_name" not in summary
        assert "subject_region" not in summary
        assert "queries" not in summary
        assert "식별정보의원" not in repr(summary)

    async def test_leads_without_a_diagnosis_get_an_empty_list(self, pg_async_session):
        """리드마그넷이 아닌 일반 상담 리드도 같은 목록에 있다 — 필드가 없으면 화면이 깨진다."""
        lead, _ = await _seed_lead(
            pg_async_session, clinic_name="일반상담의원", with_diagnosis=False
        )
        rows = await leads_api.list_sales_leads(db=pg_async_session, limit=200, offset=0)
        assert _find(rows, lead.id)["diagnoses"] == []

    async def test_needs_attention_is_computed_per_axis(self, pg_async_session):
        healthy, _ = await _seed_lead(pg_async_session, clinic_name="정상의원")
        broken, _ = await _seed_lead(
            pg_async_session,
            clinic_name="발송실패의원",
            delivery_status=DeliveryStatus.FAILED.value,
        )
        rows = await leads_api.list_sales_leads(db=pg_async_session, limit=200, offset=0)

        assert _find(rows, healthy.id)["diagnoses"][0]["needs_attention"] is False
        assert _find(rows, broken.id)["diagnoses"][0]["needs_attention"] is True


@pytest.mark.asyncio
class TestNeedsAttentionFilter:
    async def test_filter_uses_lead_triage_not_diagnosis_failure(self, pg_async_session):
        healthy, _ = await _seed_lead(pg_async_session, clinic_name="필터정상의원")
        failed_delivery, _ = await _seed_lead(
            pg_async_session,
            clinic_name="필터발송실패의원",
            delivery_status=DeliveryStatus.FAILED.value,
        )
        blocked_report, _ = await _seed_lead(
            pg_async_session,
            clinic_name="필터리포트차단의원",
            execution_status=ExecutionStatus.FAILED.value,
            report_status=ReportStatus.BLOCKED.value,
            delivery_status=DeliveryStatus.PENDING.value,
        )

        rows = await leads_api.list_sales_leads(
            db=pg_async_session, limit=200, offset=0, needs_attention=True
        )
        ids = {row["id"] for row in rows}

        assert str(failed_delivery.id) in ids
        assert str(blocked_report.id) in ids
        assert str(healthy.id) in ids

    async def test_filter_includes_new_leads_with_no_diagnosis(self, pg_async_session):
        """확인 필요의 신규 조건은 무료 진단 유무와 무관하다."""
        plain, _ = await _seed_lead(
            pg_async_session, clinic_name="필터일반의원", with_diagnosis=False
        )
        rows = await leads_api.list_sales_leads(
            db=pg_async_session, limit=200, offset=0, needs_attention=True
        )
        assert str(plain.id) in {row["id"] for row in rows}

    async def test_default_listing_still_returns_everything(self, pg_async_session):
        healthy, _ = await _seed_lead(pg_async_session, clinic_name="기본목록의원")
        rows = await leads_api.list_sales_leads(db=pg_async_session, limit=200, offset=0)
        assert str(healthy.id) in {row["id"] for row in rows}

    async def test_operations_test_lead_is_kept_but_excluded_from_real_work(self, pg_async_session):
        real, _ = await _seed_lead(pg_async_session, clinic_name="실운영신규의원")
        qa, _ = await _seed_lead(pg_async_session, clinic_name="운영점검의원")
        qa.source_path = "/ops-qa"
        qa.consent_version = "ops-qa-v1"
        qa.conversion_note = "[OPS-QA-20260824]"
        await pg_async_session.flush()

        full_rows = await leads_api.list_sales_leads(
            db=pg_async_session, limit=200, offset=0
        )
        attention_rows = await leads_api.list_sales_leads(
            db=pg_async_session, limit=200, offset=0, needs_attention=True
        )
        summary = await leads_api.get_sales_lead_summary(pg_async_session)

        assert str(qa.id) in {row["id"] for row in full_rows}
        assert str(real.id) in {row["id"] for row in attention_rows}
        assert str(qa.id) not in {row["id"] for row in attention_rows}
        assert summary["total"] >= 1
        assert summary["operations_test"] >= 1
