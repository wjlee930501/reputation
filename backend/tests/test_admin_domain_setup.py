import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from slowapi import Limiter

from app.core.database import get_db
from app.core.config import settings
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.hospital import HospitalStatus


class FakeDB:
    def __init__(self, hospital):
        self._hospital = hospital

    async def get(self, model, object_id):
        return self._hospital if object_id == self._hospital.id else None


def _hospital(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        status=HospitalStatus.PENDING_DOMAIN,
        aeo_domain="clinic.example.com",
        domain_management_mode="HOSPITAL_MANAGED",
        domain_dns_strategy="CNAME",
        domain_registrar="Gabia",
        domain_dns_provider="Cloudflare",
        domain_purchase_note="Hospital owns this domain.",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _get_setup(hospital, monkeypatch):
    async def override_get_db():
        yield FakeDB(hospital)

    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            return client.get(
                f"/api/v1/admin/hospitals/{hospital.id}/domain/setup",
                headers={"X-Admin-Key": "test-admin-key"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter


def test_domain_setup_returns_cname_plan(monkeypatch):
    monkeypatch.setattr(settings, "CNAME_TARGET", "target.motionlabs.example")
    hospital = _hospital(aeo_domain="www.clinic.example.com")

    response = _get_setup(hospital, monkeypatch)

    assert response.status_code == 200
    payload = response.json()
    assert payload["domain"] == "www.clinic.example.com"
    assert payload["domain_management_mode"] == "HOSPITAL_MANAGED"
    assert payload["domain_dns_strategy"] == "CNAME"
    assert payload["domain_registrar"] == "Gabia"
    assert payload["domain_dns_provider"] == "Cloudflare"
    assert payload["domain_purchase_note"] == "Hospital owns this domain."
    assert payload["management_mode"] == "HOSPITAL_MANAGED"
    assert payload["dns_strategy"] == "CNAME"
    assert payload["records"][0]["type"] == "CNAME"
    assert payload["records"][0]["name"] == "www.clinic.example.com"
    assert payload["records"][0]["value"] == "target.motionlabs.example"
    assert payload["records"][0]["ttl"] == "300"
    assert [step["key"] for step in payload["checklist"]] == [
        "domain_saved",
        "purchase",
        "dns_record",
        "dns_verified",
        "certificate_ready",
    ]
    assert payload["warnings"] == []


def test_domain_setup_returns_apex_address_plan(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_DOMAIN_IP_TARGETS", "34.117.10.20,2600:1901::1")
    hospital = _hospital(
        aeo_domain="clinic.example.com",
        domain_management_mode="MOTIONLABS_MANAGED",
        domain_dns_strategy="APEX_ADDRESS",
        domain_registrar=None,
        domain_dns_provider=None,
        domain_purchase_note=None,
    )

    response = _get_setup(hospital, monkeypatch)

    assert response.status_code == 200
    payload = response.json()
    assert payload["domain_management_mode"] == "MOTIONLABS_MANAGED"
    assert payload["domain_dns_strategy"] == "APEX_ADDRESS"
    assert [(record["type"], record["name"], record["value"]) for record in payload["records"]] == [
        ("A", "clinic.example.com", "34.117.10.20"),
        ("AAAA", "clinic.example.com", "2600:1901::1"),
    ]
    assert payload["warnings"] == []


def test_domain_setup_warns_when_apex_has_no_ip_targets(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_DOMAIN_IP_TARGETS", "")
    hospital = _hospital(domain_dns_strategy="APEX_ADDRESS")

    response = _get_setup(hospital, monkeypatch)

    assert response.status_code == 200
    payload = response.json()
    assert payload["records"] == []
    assert payload["warnings"] == [
        "APEX_ADDRESS strategy is selected, but CUSTOM_DOMAIN_IP_TARGETS is not configured."
    ]
