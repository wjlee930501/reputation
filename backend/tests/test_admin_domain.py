"""P1-6 — POST /domain/verify가 operations verify-domain과 동일한 LIVE 게이트를 갖는지."""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.api.admin import domain as domain_api
from app.models.handoff import HandoffState, HospitalHandoff
from app.models.hospital import DomainDnsStrategy, HospitalStatus
from app.models.monthly_control import HospitalServiceInterval
from app.workers import tasks


class FakeDB:
    def __init__(self, hospital, *, handoff_state=HandoffState.HANDOFF_ACCEPTED):
        self.hospital = hospital
        self.handoff_state = handoff_state
        self.committed = False
        self.added = []

    async def get(self, model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    async def scalar(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is HospitalHandoff:
            return self.handoff_state
        if entity is HospitalServiceInterval:
            return None
        return None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True


def test_live_domain_check_rejects_redirect_without_following_it():
    requests: list[httpx.Request] = []

    def redirecting_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://unrelated.example.net/"},
            request=request,
        )

    with httpx.Client(
        transport=httpx.MockTransport(redirecting_handler),
        follow_redirects=False,
        timeout=httpx.Timeout(10.0, connect=5.0),
    ) as client:
        healthy, reason = tasks._check_custom_domain_https(
            client,
            "clinic.example.com",
            expected_hospital_id=uuid.uuid4(),
            expected_slug="jang-clinic",
        )

    assert healthy is False
    assert reason == "redirect_not_allowed"
    assert requests[0].url.path == "/.well-known/reputation-health"


def test_live_domain_check_rejects_another_hospital_marker():
    expected_hospital_id = uuid.uuid4()

    def wrong_tenant_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hospital_id": str(uuid.uuid4()),
                "slug": "other-clinic",
                "canonical_host": "clinic.example.com",
                "release": "site-r17",
            },
            request=request,
        )

    with httpx.Client(
        transport=httpx.MockTransport(wrong_tenant_handler),
        follow_redirects=False,
        timeout=httpx.Timeout(10.0, connect=5.0),
    ) as client:
        healthy, reason = tasks._check_custom_domain_https(
            client,
            "clinic.example.com",
            expected_hospital_id=expected_hospital_id,
            expected_slug="jang-clinic",
        )

    assert healthy is False
    assert reason == "tenant_marker_mismatch"


def _hospital(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="테스트의원",
        status=HospitalStatus.PENDING_DOMAIN,
        aeo_domain="clinic.example.com",
        v0_report_done=True,
        site_built=True,
        schedule_set=True,
        site_live=False,
        profile_complete=True,
        domain_cert_job_state=None,
        domain_cert_job_started_at=None,
        domain_cert_dns_verified_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_dns(monkeypatch, cname="target.motionlabs.io"):
    monkeypatch.setattr(domain_api.settings, "CNAME_TARGET", "target.motionlabs.io")
    monkeypatch.setattr(domain_api.settings, "CUSTOM_DOMAIN_IP_TARGETS", "")
    monkeypatch.setattr(domain_api.settings, "CERTIFICATE_MANAGER_AUTO_PROVISION", False)

    async def _fake_resolve(domain):
        return cname

    monkeypatch.setattr(domain_api, "_resolve_cname", _fake_resolve)

    async def _fake_resolve_addresses(domain):
        return []

    monkeypatch.setattr(domain_api, "_resolve_addresses", _fake_resolve_addresses)


async def test_verify_domain_activates_when_all_prerequisites_met(monkeypatch):
    hospital = _hospital()
    db = FakeDB(hospital)
    _patch_dns(monkeypatch)

    response = await domain_api.verify_domain(hospital.id, db=db)

    assert response.verified is True
    assert response.domain == "clinic.example.com"
    assert response.expected_cname == "target.motionlabs.io"
    assert hospital.site_live is True
    assert hospital.status == HospitalStatus.ACTIVE
    assert db.committed is True
    # LIVE 전환은 감사 로그에 기록된다 (operations 경로와 정합).
    assert any(getattr(item, "action", None) == "verify_domain" for item in db.added)


@pytest.mark.parametrize(
    "overrides,expected_label",
    [
        ({"profile_complete": False}, "병원 기본 정보 완료"),
    ],
)
async def test_verify_domain_blocks_live_without_profile_complete(
    monkeypatch, overrides, expected_label
):
    """프로파일 미완료면 DNS가 맞아도 LIVE 전환 불가 — 프로파일 게이트 우회 차단."""
    hospital = _hospital(**overrides)
    db = FakeDB(hospital)
    _patch_dns(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        await domain_api.verify_domain(hospital.id, db=db)

    assert exc_info.value.status_code == 409
    assert expected_label in [item["label"] for item in exc_info.value.detail["prerequisites"]]
    assert hospital.site_live is False
    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    assert db.committed is False


@pytest.mark.parametrize(
    "overrides,expected_label",
    [
        ({"v0_report_done": False}, "초기 진단 리포트"),
        ({"site_built": False}, "콘텐츠 허브 준비"),
    ],
)
async def test_verify_domain_blocks_live_without_prerequisites(
    monkeypatch, overrides, expected_label
):
    """DNS가 맞아도 STEP 5 사전 단계 미충족이면 409 — 게이트 우회 차단."""
    hospital = _hospital(**overrides)
    db = FakeDB(hospital)
    _patch_dns(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        await domain_api.verify_domain(hospital.id, db=db)

    assert exc_info.value.status_code == 409
    assert expected_label in [item["label"] for item in exc_info.value.detail["prerequisites"]]
    assert hospital.site_live is False
    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    assert db.committed is False


async def test_verify_domain_keeps_response_shape_on_cname_mismatch(monkeypatch):
    """CNAME 불일치는 기존 응답 형태(verified=False + 안내 메시지)를 유지한다."""
    hospital = _hospital(v0_report_done=False)  # 게이트보다 DNS 실패가 먼저
    db = FakeDB(hospital)
    _patch_dns(monkeypatch, cname="wrong.example.net")

    response = await domain_api.verify_domain(hospital.id, db=db)

    assert response.verified is False
    assert "DNS 검증 실패" in response.message
    assert hospital.site_live is False
    assert db.committed is False


async def test_verify_domain_waits_for_https_certificate_after_dns_is_ready(monkeypatch):
    # DM-F4 new contract: DNS 검증 성공 = 온보딩 5단계 완료, 인증서는 후속 작업
    hospital = _hospital()
    db = FakeDB(hospital)
    _patch_dns(monkeypatch)
    monkeypatch.setattr(domain_api.settings, "CERTIFICATE_MANAGER_AUTO_PROVISION", True)

    async def _pending_certificate(domain):
        assert domain == "clinic.example.com"
        return SimpleNamespace(
            ready=False,
            phase="PROVISIONING",
            message="HTTPS 인증서를 준비하고 있습니다.",
            error_code=None,
        )

    monkeypatch.setattr(domain_api, "ensure_verified_domain_certificate", _pending_certificate)

    response = await domain_api.verify_domain(hospital.id, db=db)

    # DNS 검증 성공 = operator step 5 done
    assert response.dns_verified is True
    assert response.verified is True  # DM-F4: DNS success means verified
    assert response.certificate_ready is False
    assert response.cert_job_state == "ISSUING"  # Live contract: cert job state is ISSUING
    assert hospital.site_live is True  # DM-F4: site goes live on DNS success
    assert hospital.status == HospitalStatus.ACTIVE  # DM-F4: status becomes ACTIVE
    assert db.committed is True
    assert db.added[0].action == "provision_domain_certificate"


async def test_verify_domain_accepts_lb_address_for_apex_domain(monkeypatch):
    hospital = _hospital(
        aeo_domain="jangclinic.co.kr", domain_dns_strategy=DomainDnsStrategy.APEX_ADDRESS
    )
    db = FakeDB(hospital)
    monkeypatch.setattr(domain_api.settings, "CNAME_TARGET", "target.motionlabs.io")
    monkeypatch.setattr(domain_api.settings, "CUSTOM_DOMAIN_IP_TARGETS", "34.117.10.20")

    async def _fake_resolve_cname(domain):
        return None

    async def _fake_resolve_addresses(domain):
        return ["34.117.10.20"]

    monkeypatch.setattr(domain_api, "_resolve_cname", _fake_resolve_cname)
    monkeypatch.setattr(domain_api, "_resolve_addresses", _fake_resolve_addresses)

    response = await domain_api.verify_domain(hospital.id, db=db)

    assert response.verified is True
    assert response.verification_method == "address"
    assert response.address_values == ["34.117.10.20"]
    assert hospital.site_live is True
    assert hospital.status == HospitalStatus.ACTIVE
    assert db.committed is True


async def test_verify_domain_uses_selected_apex_strategy_even_when_cname_matches(monkeypatch):
    hospital = _hospital(
        aeo_domain="jangclinic.co.kr", domain_dns_strategy=DomainDnsStrategy.APEX_ADDRESS
    )
    db = FakeDB(hospital)
    monkeypatch.setattr(domain_api.settings, "CNAME_TARGET", "target.motionlabs.io")
    monkeypatch.setattr(domain_api.settings, "CUSTOM_DOMAIN_IP_TARGETS", "34.117.10.20")

    async def _fake_resolve_cname(domain):
        return "target.motionlabs.io"

    async def _fake_resolve_addresses(domain):
        return []

    monkeypatch.setattr(domain_api, "_resolve_cname", _fake_resolve_cname)
    monkeypatch.setattr(domain_api, "_resolve_addresses", _fake_resolve_addresses)

    response = await domain_api.verify_domain(hospital.id, db=db)

    assert response.verified is False
    assert response.verification_method is None
    assert "A/AAAA" in response.message
    assert hospital.site_live is False
    assert db.committed is False


async def test_verify_domain_rejects_apex_when_cname_exists_even_if_address_matches(monkeypatch):
    hospital = _hospital(
        aeo_domain="jangclinic.co.kr", domain_dns_strategy=DomainDnsStrategy.APEX_ADDRESS
    )
    db = FakeDB(hospital)
    monkeypatch.setattr(domain_api.settings, "CNAME_TARGET", "target.motionlabs.io")
    monkeypatch.setattr(domain_api.settings, "CUSTOM_DOMAIN_IP_TARGETS", "34.117.10.20")

    async def _fake_resolve_cname(domain):
        return "target.motionlabs.io"

    async def _fake_resolve_addresses(domain):
        return ["34.117.10.20"]

    monkeypatch.setattr(domain_api, "_resolve_cname", _fake_resolve_cname)
    monkeypatch.setattr(domain_api, "_resolve_addresses", _fake_resolve_addresses)

    response = await domain_api.verify_domain(hospital.id, db=db)

    assert response.verified is False
    assert response.verification_method is None
    assert hospital.site_live is False
    assert db.committed is False


async def test_verify_domain_requires_domain_set():
    hospital = _hospital(aeo_domain=None)
    db = FakeDB(hospital)

    with pytest.raises(HTTPException) as exc_info:
        await domain_api.verify_domain(hospital.id, db=db)

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    ("missing_key", "overrides", "handoff_state"),
    [
        ("profile_complete", {"profile_complete": False}, HandoffState.HANDOFF_ACCEPTED),
        ("v0_report_done", {"v0_report_done": False}, HandoffState.HANDOFF_ACCEPTED),
        ("site_built", {"site_built": False}, HandoffState.HANDOFF_ACCEPTED),
    ],
)
async def test_verify_domain_blocks_each_authoritative_gate(
    monkeypatch, missing_key, overrides, handoff_state
):
    hospital = _hospital(**overrides)
    db = FakeDB(hospital, handoff_state=handoff_state)
    _patch_dns(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await domain_api.verify_domain(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["missing"] == [missing_key]
    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    assert hospital.site_live is False
    assert not any(isinstance(item, HospitalServiceInterval) for item in db.added)
