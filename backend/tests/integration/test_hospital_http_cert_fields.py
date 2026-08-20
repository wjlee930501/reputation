"""B1: Hospital list and detail HTTP responses must include domain_cert_* fields.

Real HTTP integration test against /api/v1/admin/hospitals.
"""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.models.hospital import DomainCertJobState, Hospital, HospitalStatus


@pytest.mark.asyncio
async def test_hospital_list_http_includes_cert_fields(pg_async_session):
    """GET /api/v1/admin/hospitals JSON response contains domain_cert_* fields."""
    hospital = Hospital(
        id=uuid.uuid4(),
        name="리스트테스트의원",
        slug=f"http-list-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
        aeo_domain="list-test.example.com",
        domain_cert_job_state=DomainCertJobState.ISSUING,
        domain_cert_dns_verified_at=datetime.now(UTC),
    )
    pg_async_session.add(hospital)
    await pg_async_session.commit()
    
    async def override_db():
        yield pg_async_session
    
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/v1/admin/hospitals",
                headers={"X-Admin-Key": "test-admin-key"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert isinstance(data, list)
    
    # Find our hospital
    item = next((h for h in data if h["slug"] == hospital.slug), None)
    assert item is not None, f"Hospital {hospital.slug} not found in list response"
    
    # B1: cert fields must be present
    assert "domain_cert_job_state" in item
    assert "domain_cert_dns_verified_at" in item
    assert item["domain_cert_job_state"] == "ISSUING"
    assert item["domain_cert_dns_verified_at"] is not None


@pytest.mark.asyncio
async def test_hospital_detail_http_includes_all_cert_fields(pg_async_session):
    """GET /api/v1/admin/hospitals/{id} JSON response contains all cert tracking fields."""
    hospital_id = uuid.uuid4()
    hospital = Hospital(
        id=hospital_id,
        name="상세테스트의원",
        slug=f"http-detail-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
        aeo_domain="detail-test.example.com",
        domain_cert_job_state=DomainCertJobState.DONE,
        domain_cert_dns_verified_at=datetime.now(UTC),
        domain_cert_job_started_at=datetime.now(UTC),
    )
    pg_async_session.add(hospital)
    await pg_async_session.commit()
    
    async def override_db():
        yield pg_async_session
    
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/admin/hospitals/{hospital_id}",
                headers={"X-Admin-Key": "test-admin-key"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    
    # B1: all cert tracking fields must be present
    assert "domain_cert_job_state" in data
    assert "domain_cert_dns_verified_at" in data
    assert "domain_cert_job_started_at" in data
    assert data["domain_cert_job_state"] == "DONE"
    assert data["domain_cert_dns_verified_at"] is not None
    assert data["domain_cert_job_started_at"] is not None
