"""Test that hospital list and detail HTTP endpoints include cert tracking fields.

B1: FastAPI response_model must not strip domain_cert_job_state / domain_cert_job_started_at / domain_cert_dns_verified_at.
"""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.hospital import DomainCertJobState, Hospital, HospitalStatus


class FakeDB:
    """Minimal async DB mock."""
    
    def __init__(self, hospitals: list[Hospital]):
        self.hospitals = {h.id: h for h in hospitals}
        self.committed = False

    async def execute(self, stmt):
        class Result:
            def scalars(self):
                class Scalars:
                    def all(self):
                        return list(self.hospitals.values())
                return Scalars()
        return Result()

    async def get(self, model, object_id):
        return self.hospitals.get(object_id)

    async def commit(self):
        self.committed = True


@pytest.fixture
def client():
    return TestClient(app)


def test_hospital_list_includes_cert_fields(client, monkeypatch):
    """GET /admin/hospitals JSON response contains domain_cert_* fields."""
    hospital = Hospital(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        status=HospitalStatus.ACTIVE,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
        aeo_domain="test.example.com",
        domain_cert_job_state=DomainCertJobState.ISSUING.value,
        domain_cert_dns_verified_at=datetime.now(UTC),
    )
    
    db = FakeDB([hospital])
    
    async def mock_get_db():
        yield db
    
    from app.core import database
    monkeypatch.setattr(database, "get_db", mock_get_db)
    
    response = client.get("/admin/hospitals")
    
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    
    item = data[0]
    assert "domain_cert_job_state" in item
    assert "domain_cert_dns_verified_at" in item
    assert item["domain_cert_job_state"] == "ISSUING"
    assert item["domain_cert_dns_verified_at"] is not None


def test_hospital_detail_includes_cert_fields(client, monkeypatch):
    """GET /admin/hospitals/{id} JSON response contains all cert tracking fields."""
    hospital_id = uuid.uuid4()
    hospital = Hospital(
        id=hospital_id,
        name="상세테스트의원",
        slug="detail-test",
        status=HospitalStatus.ACTIVE,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
        aeo_domain="detail.example.com",
        domain_cert_job_state=DomainCertJobState.DONE.value,
        domain_cert_dns_verified_at=datetime.now(UTC),
        domain_cert_job_started_at=datetime.now(UTC),
    )
    
    db = FakeDB([hospital])
    
    async def mock_get_db():
        yield db
    
    from app.core import database
    monkeypatch.setattr(database, "get_db", mock_get_db)
    
    response = client.get(f"/admin/hospitals/{hospital_id}")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "domain_cert_job_state" in data
    assert "domain_cert_dns_verified_at" in data
    assert "domain_cert_job_started_at" in data
    assert data["domain_cert_job_state"] == "DONE"
    assert data["domain_cert_dns_verified_at"] is not None
    assert data["domain_cert_job_started_at"] is not None
