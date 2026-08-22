import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from slowapi import Limiter

from app.api.admin import domain_setup as domain_setup_api
from app.core.config import settings
from app.core.database import get_db
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
    hospital = _hospital(aeo_domain="www.clinic.example.com", site_live=False, domain_cert_dns_verified_at=None, domain_cert_job_state=None)

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
    assert payload["records"][0]["host"] == "www.clinic.example.com"
    assert payload["records"][0]["registrar_host"] == "www"
    assert payload["records"][0]["value"] == "target.motionlabs.example."  # DM-U2: trailing dot
    assert payload["records"][0]["ttl"] == "300 (또는 등록기관 최소값)"  # DM-U1
    assert [step["key"] for step in payload["checklist"]] == [
        "domain_saved",
        "purchase",
        "dns_record",
        "dns_verified",
        "certificate_ready",
    ]
    # DM-U1/DM-F5: warnings include TTL 검증 무관 and Gabia
    assert len(payload["warnings"]) == 3
    assert any("TTL은 DNS 검증 속도에 영향을 주지 않습니다" in w for w in payload["warnings"])
    assert any("Gabia" in w and "확인 후 저장" in w for w in payload["warnings"])


def test_domain_setup_reads_certificate_state_without_provider_call(monkeypatch):
    monkeypatch.setattr(settings, "CERTIFICATE_MANAGER_AUTO_PROVISION", True)
    assert not hasattr(domain_setup_api, "inspect_domain_certificate")
    hospital = _hospital(
        site_live=True,
        domain_cert_dns_verified_at="2026-08-21T00:00:00Z",
        domain_cert_job_state="ISSUING",
    )

    with patch(
        "app.services.domain_certificate_manager.inspect_domain_certificate"
    ) as inspect_certificate:
        response = _get_setup(hospital, monkeypatch)

    inspect_certificate.assert_not_called()

    assert response.status_code == 200
    payload = response.json()
    assert payload["certificate_ready"] is False
    assert payload["certificate_phase"] == "ISSUING"
    certificate_step = next(
        step for step in payload["checklist"] if step["key"] == "certificate_ready"
    )
    assert certificate_step["status"] == "WAITING"


def test_domain_setup_returns_apex_address_plan(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_DOMAIN_IP_TARGETS", "34.117.10.20,2600:1901::1")
    hospital = _hospital(
        aeo_domain="clinic.example.com",
        domain_management_mode="MOTIONLABS_MANAGED",
        domain_dns_strategy="APEX_ADDRESS",
        domain_registrar=None,
        domain_dns_provider=None,
        domain_purchase_note=None,
        site_live=False,
        domain_cert_dns_verified_at=None,
        domain_cert_job_state=None,
    )

    response = _get_setup(hospital, monkeypatch)

    assert response.status_code == 200
    payload = response.json()
    assert payload["domain_management_mode"] == "MOTIONLABS_MANAGED"
    assert payload["domain_dns_strategy"] == "APEX_ADDRESS"
    assert [(record["type"], record["name"], record["value"], record["registrar_host"]) for record in payload["records"]] == [
        ("A", "clinic.example.com", "34.117.10.20", "@"),
        ("AAAA", "clinic.example.com", "2600:1901::1", "@"),
    ]
    # DM-U1/DM-F5: warnings include TTL and Gabia
    assert len(payload["warnings"]) == 3


def test_domain_setup_warns_when_apex_has_no_ip_targets(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_DOMAIN_IP_TARGETS", "")
    hospital = _hospital(domain_dns_strategy="APEX_ADDRESS", site_live=False, domain_cert_dns_verified_at=None, domain_cert_job_state=None)

    response = _get_setup(hospital, monkeypatch)

    assert response.status_code == 200
    payload = response.json()
    assert payload["records"] == []
    # DM-U1/DM-F5: base warnings + config warning
    assert len(payload["warnings"]) == 4
    assert any("APEX_ADDRESS strategy is selected, but CUSTOM_DOMAIN_IP_TARGETS is not configured" in w for w in payload["warnings"])


def test_domain_setup_tracker_follows_the_last_live_check(monkeypatch):
    """A-1: 실제로 응답하는 도메인이 'DNS 검증 필요 / HTTPS 필요'로 남지 않는다."""
    monkeypatch.setattr(settings, "CNAME_TARGET", "target.motionlabs.example")
    # 도메인을 다시 저장하면 domain_cert_* 는 초기화된다. 그때도 마지막 관측이 정상이면
    # 트래커는 그 사실을 따라야 한다.
    hospital = _hospital(
        site_live=True,
        domain_cert_dns_verified_at=None,
        domain_cert_job_state=None,
        domain_last_checked_at=datetime(2026, 8, 22, 3, 0, tzinfo=timezone.utc),
        domain_last_check_ok=True,
        domain_last_check_reason="tenant_marker_ok",
    )

    response = _get_setup(hospital, monkeypatch)

    assert response.status_code == 200
    payload = response.json()
    assert payload["last_check_ok"] is True
    assert payload["last_checked_at"].startswith("2026-08-22T03:00:00")
    assert payload["last_check_reason"] == "tenant_marker_ok"
    statuses = {item["key"]: item["status"] for item in payload["checklist"]}
    assert statuses["dns_record"] == "DONE"
    assert statuses["dns_verified"] == "DONE"
    assert statuses["certificate_ready"] == "DONE"


def test_domain_setup_tracker_stays_pending_when_the_last_check_failed(monkeypatch):
    monkeypatch.setattr(settings, "CNAME_TARGET", "target.motionlabs.example")
    hospital = _hospital(
        site_live=False,
        domain_cert_dns_verified_at=None,
        domain_cert_job_state=None,
        domain_last_checked_at=datetime(2026, 8, 22, 3, 0, tzinfo=timezone.utc),
        domain_last_check_ok=False,
        domain_last_check_reason="tls_or_network_error",
    )

    response = _get_setup(hospital, monkeypatch)

    payload = response.json()
    assert payload["last_check_ok"] is False
    statuses = {item["key"]: item["status"] for item in payload["checklist"]}
    assert statuses["dns_verified"] == "PENDING"
    assert statuses["certificate_ready"] == "PENDING"
