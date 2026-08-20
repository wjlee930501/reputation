"""Test that hospital list and detail HTTP endpoints include cert tracking fields.

B1: FastAPI response_model must not strip domain_cert_* fields.
Real route mount: /api/v1/admin/hospitals
"""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.hospital import DomainCertJobState, Hospital, HospitalStatus


@pytest.mark.asyncio
async def test_hospital_list_http_response_includes_cert_fields(async_db_session: AsyncSession):
    """GET /api/v1/admin/hospitals JSON response contains domain_cert_* fields."""
    hospital = Hospital(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-http-list",
        status=HospitalStatus.ACTIVE,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
        aeo_domain="list-test.example.com",
        domain_cert_job_state=DomainCertJobState.ISSUING.value,
        domain_cert_dns_verified_at=datetime.now(UTC),
    )
    async_db_session.add(hospital)
    await async_db_session.commit()
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/v1/admin/hospitals")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert isinstance(data, list)
    
    # Find our hospital
    item = next((h for h in data if h["slug"] == "test-http-list"), None)
    assert item is not None, "Hospital not found in list response"
    
    # B1: cert fields must be present
    assert "domain_cert_job_state" in item
    assert "domain_cert_dns_verified_at" in item
    assert item["domain_cert_job_state"] == "ISSUING"
    assert item["domain_cert_dns_verified_at"] is not None


@pytest.mark.asyncio
async def test_hospital_detail_http_response_includes_all_cert_fields(async_db_session: AsyncSession):
    """GET /api/v1/admin/hospitals/{id} JSON response contains all cert tracking fields."""
    hospital_id = uuid.uuid4()
    hospital = Hospital(
        id=hospital_id,
        name="상세테스트의원",
        slug="detail-http-test",
        status=HospitalStatus.ACTIVE,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
        aeo_domain="detail-test.example.com",
        domain_cert_job_state=DomainCertJobState.DONE.value,
        domain_cert_dns_verified_at=datetime.now(UTC),
        domain_cert_job_started_at=datetime.now(UTC),
    )
    async_db_session.add(hospital)
    await async_db_session.commit()
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get(f"/api/v1/admin/hospitals/{hospital_id}")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    
    # B1: all cert tracking fields must be present
    assert "domain_cert_job_state" in data
    assert "domain_cert_dns_verified_at" in data
    assert "domain_cert_job_started_at" in data
    assert data["domain_cert_job_state"] == "DONE"
    assert data["domain_cert_dns_verified_at"] is not None
    assert data["domain_cert_job_started_at"] is not None
