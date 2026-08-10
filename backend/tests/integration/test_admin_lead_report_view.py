import uuid
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_db
from app.main import app
from app.models.admin_user import ROLE_OPERATOR, AdminUser
from app.models.audit import AdminAuditLog
from app.models.lead import SalesLead
from app.models.lead_diagnosis import LeadDiagnosis, LeadReportArtifact


async def _seed_ready_report(session, pdf_path: str):
    actor = AdminUser(
        email=f"report-view-{uuid.uuid4().hex[:8]}@example.com",
        name="리포트 검수자",
        role=ROLE_OPERATOR,
        password_hash="not-used",
        is_active=True,
    )
    lead = SalesLead(
        clinic_name="리포트조회검증의원",
        clinic_type="내과",
        contact="010-0000-0000",
        email="applicant@example.com",
        privacy=True,
        source="AI_DIAGNOSIS",
    )
    session.add_all((actor, lead))
    await session.flush()
    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        applicant_email_hash=uuid.uuid4().hex,
        subject_phone_hash=uuid.uuid4().hex,
        subject_hospital_name=lead.clinic_name,
        subject_region="서울",
        slot_date=date(2099, 1, 1),
        slot_no=uuid.uuid4().int % 1_000_000 + 1_000,
        queries=[{"slot": 1, "kind": "지역형", "text": "서울 내과 추천"}],
        requested_models={"openai": "model-a", "gemini": "model-b", "judge": "model-c"},
        repeat_count=3,
        execution_status="SUCCEEDED",
        report_status="READY",
        delivery_status="SENT",
    )
    session.add(diagnosis)
    await session.flush()
    session.add(
        LeadReportArtifact(
            diagnosis_id=diagnosis.id,
            version=1,
            storage_uri=pdf_path,
            content_hash="a" * 64,
            byte_size=16,
            template_version="test-v1",
        )
    )
    await session.flush()
    return actor, lead, diagnosis


@pytest.mark.asyncio
async def test_active_operator_can_open_ready_report(pg_async_session, tmp_path):
    pdf_path = tmp_path / "lead-report.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nreport")
    actor, lead, diagnosis = await _seed_ready_report(pg_async_session, str(pdf_path))

    async def override_db():
        yield pg_async_session

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/admin/leads/{lead.id}/diagnoses/{diagnosis.id}/report",
                headers={"X-Admin-Key": "test-admin-key", "X-Admin-Actor": actor.email},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.content == b"%PDF-1.7\nreport"
    audit = (
        await pg_async_session.execute(
            select(AdminAuditLog).where(
                AdminAuditLog.action == "view_lead_diagnosis_report",
                AdminAuditLog.target_id == str(diagnosis.id),
            )
        )
    ).scalar_one()
    assert audit.actor == actor.email
    assert audit.detail == {"artifact_version": 1}


@pytest.mark.asyncio
async def test_report_view_is_scoped_to_its_lead(pg_async_session, tmp_path):
    pdf_path = tmp_path / "lead-report.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nreport")
    actor, _, diagnosis = await _seed_ready_report(pg_async_session, str(pdf_path))

    async def override_db():
        yield pg_async_session

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/admin/leads/{uuid.uuid4()}/diagnoses/{diagnosis.id}/report",
                headers={"X-Admin-Key": "test-admin-key", "X-Admin-Actor": actor.email},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_report_view_requires_an_active_operator(pg_async_session, tmp_path):
    pdf_path = tmp_path / "lead-report.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nreport")
    _, lead, diagnosis = await _seed_ready_report(pg_async_session, str(pdf_path))

    async def override_db():
        yield pg_async_session

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/admin/leads/{lead.id}/diagnoses/{diagnosis.id}/report",
                headers={"X-Admin-Key": "test-admin-key"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("report_status", "expected_status"),
    [("BLOCKED", 409), ("PURGED", 410)],
)
@pytest.mark.asyncio
async def test_report_view_fails_closed_when_report_is_not_ready(
    pg_async_session,
    tmp_path,
    report_status,
    expected_status,
):
    pdf_path = tmp_path / "lead-report.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nreport")
    actor, lead, diagnosis = await _seed_ready_report(pg_async_session, str(pdf_path))
    diagnosis.report_status = report_status
    diagnosis.delivery_status = "PENDING"
    await pg_async_session.flush()

    async def override_db():
        yield pg_async_session

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/admin/leads/{lead.id}/diagnoses/{diagnosis.id}/report",
                headers={"X-Admin-Key": "test-admin-key", "X-Admin-Actor": actor.email},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == expected_status
