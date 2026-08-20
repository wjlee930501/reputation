"""Test certificate job tracking and DNS-verified onboarding completion.

Tests for the domain onboarding step 5 fix:
- DM-F1: Certificate job state tracking (waiting/issuing/done/failed)
- DM-F2: In-flight certificate provision is idempotent (409 if duplicate)
- DM-F4: DNS verification success = operator-complete for step 5, cert is follow-up
"""

import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anyio
import pytest
from fastapi import HTTPException

from app.api.admin import domain as domain_api
from app.api.admin.domain import verify_domain
from app.models.hospital import DomainCertJobState, Hospital, HospitalStatus
from app.services import domain_certificate_manager
from app.services.domain_certificate_manager import DomainCertificateResult


class FakeDB:
    """Minimal async DB mock for domain verify tests."""
    
    def __init__(self, hospital):
        self.hospital = hospital
        self.committed = False
        self.added = []

    async def get(self, model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    async def scalar(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is Hospital:
            return self.hospital
        return None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True


class FakeCertificateTask:
    def __init__(self, db: FakeDB):
        self.db = db
        self.calls: list[dict[str, object]] = []

    def apply_async(self, *, args, queue, headers):
        self.calls.append(
            {
                "args": args,
                "queue": queue,
                "headers": headers,
                "committed": self.db.committed,
            }
        )
        return SimpleNamespace(id="certificate-task")


def make_hospital(
    aeo_domain="ai.testclinic.co.kr",
    status=HospitalStatus.PENDING_DOMAIN,
    profile_complete=True,
    v0_report_done=True,
    site_built=True,
    site_live=False,
    cert_job_state=None,
    cert_job_started_at=None,
    dns_verified_at=None,
):
    """Create test hospital with domain tracking fields."""
    from app.models.hospital import DomainDnsStrategy, DomainManagementMode
    
    hospital = Hospital(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        aeo_domain=aeo_domain,
        status=status,
        profile_complete=profile_complete,
        v0_report_done=v0_report_done,
        site_built=site_built,
        site_live=site_live,
        domain_dns_strategy=DomainDnsStrategy.CNAME,  # Default strategy
        domain_management_mode=DomainManagementMode.HOSPITAL_MANAGED,  # Default mode
    )
    hospital.domain_cert_job_state = cert_job_state
    hospital.domain_cert_job_started_at = cert_job_started_at
    hospital.domain_cert_dns_verified_at = dns_verified_at
    hospital.domain_cert_job_domain = aeo_domain if cert_job_state else None
    hospital.domain_cert_job_token = (
        str(uuid.uuid4()) if cert_job_state == DomainCertJobState.ISSUING.value else None
    )
    return hospital


@pytest.fixture(autouse=True)
def _stub_certificate_dispatch(monkeypatch):
    monkeypatch.setattr(
        domain_api.provision_domain_certificate,
        "apply_async",
        lambda **_kwargs: None,
    )


@pytest.mark.asyncio
async def test_dns_verification_queues_certificate_without_waiting_for_provider(
    monkeypatch,
) -> None:
    hospital = make_hospital(site_live=False)
    db = FakeDB(hospital)
    task = FakeCertificateTask(db)
    provider_called = False

    async def successful_dns(*_args):
        return MagicMock(
            verified=True,
            cname_value="cname.reputation.motionlabs.kr",
            address_values=[],
            expected_cname="cname.reputation.motionlabs.kr",
            expected_addresses=[],
            verification_method="cname",
        )

    async def ready_gate(*_args):
        return {"ready": True}

    async def slow_provider(_domain: str):
        nonlocal provider_called
        provider_called = True
        await anyio.sleep(0.1)
        return DomainCertificateResult(
            hostname=hospital.aeo_domain or "",
            ready=False,
            phase="PROVISIONING",
            certificate_state="PROVISIONING",
            map_entry_state="PENDING",
            certificate_name="certificate",
            map_entry_name="entry",
            message="test",
        )

    monkeypatch.setattr(domain_api, "check_domain_dns", successful_dns)
    monkeypatch.setattr(domain_api, "evaluate_activation_gate", ready_gate)
    monkeypatch.setattr(
        domain_api,
        "ensure_verified_domain_certificate",
        slow_provider,
        raising=False,
    )
    monkeypatch.setattr(domain_api, "provision_domain_certificate", task, raising=False)

    started = time.perf_counter()
    result = await verify_domain(hospital.id, db)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.05
    assert provider_called is False
    assert result.dns_verified is True
    assert len(task.calls) == 1
    assert task.calls[0]["committed"] is True
    assert task.calls[0]["queue"] == "certificates"


@pytest.mark.asyncio
async def test_cert_status_never_calls_certificate_manager_or_expires_worker_job(
    monkeypatch,
) -> None:
    started_at = datetime.now(UTC) - timedelta(minutes=11)
    hospital = make_hospital(
        site_live=True,
        status=HospitalStatus.ACTIVE,
        cert_job_state=DomainCertJobState.ISSUING.value,
        cert_job_started_at=started_at,
        dns_verified_at=started_at,
    )
    hospital.domain_cert_job_token = str(uuid.uuid4())
    hospital.domain_cert_job_domain = hospital.aeo_domain
    db = FakeDB(hospital)

    def must_not_inspect(_domain: str):
        raise AssertionError("cert-status must read DB state instead of calling GCP")

    monkeypatch.setattr(
        domain_certificate_manager,
        "inspect_domain_certificate",
        must_not_inspect,
    )

    result = await domain_api.check_cert_status(hospital.id, db)

    assert result.cert_job_state == DomainCertJobState.ISSUING.value
    assert result.certificate_ready is False
    assert hospital.domain_cert_job_state == DomainCertJobState.ISSUING.value
    assert hospital.domain_cert_job_token is not None
    assert db.committed is False


@pytest.mark.asyncio
async def test_dns_verified_activates_site_live_without_cert():
    """DM-F4: DNS verification success → site_live=True, 인증서는 후속 작업."""
    hospital = make_hospital(site_live=False)
    db = FakeDB(hospital)
    
    # Mock DNS check to succeed
    with patch("app.api.admin.domain.check_domain_dns") as mock_dns:
        mock_dns.return_value = MagicMock(
            verified=True,
            cname_value="cname.reputation.motionlabs.kr",
            address_values=[],
            expected_cname="cname.reputation.motionlabs.kr",
            expected_addresses=[],
            verification_method="cname",
        )
        
        # Mock activation gate to pass
        with patch("app.api.admin.domain.evaluate_activation_gate") as mock_gate:
            mock_gate.return_value = {"ready": True}
            
            result = await verify_domain(hospital.id, db)
    
    # DNS 검증 성공 → site_live=True, status=ACTIVE (인증서 기다리지 않음)
    assert hospital.site_live is True
    assert hospital.status == HospitalStatus.ACTIVE
    assert hospital.domain_cert_dns_verified_at is not None
    
    # 인증서 작업은 ISSUING 상태
    assert hospital.domain_cert_job_state == DomainCertJobState.ISSUING.value
    assert hospital.domain_cert_job_started_at is not None
    
    # 응답에서 DNS verified=True, certificate_ready=False
    assert result.dns_verified is True
    assert result.verified is True  # DNS verified = onboarding complete
    assert result.certificate_ready is False
    assert result.cert_job_state == DomainCertJobState.ISSUING.value
    assert db.committed is True


@pytest.mark.asyncio
async def test_cert_job_issuing_returns_409_idempotent():
    """DM-F2: 인증서 발급 작업이 이미 진행 중이면 409 반환 (멱등성)."""
    now = datetime.now(UTC)
    started_10_min_ago = now - timedelta(minutes=10)
    
    hospital = make_hospital(
        site_live=True,  # 이미 DNS 검증 완료 + 활성화됨
        status=HospitalStatus.ACTIVE,
        cert_job_state=DomainCertJobState.ISSUING.value,
        cert_job_started_at=started_10_min_ago,
        dns_verified_at=started_10_min_ago,
    )
    db = FakeDB(hospital)
    
    # Mock DNS check to succeed
    with patch("app.api.admin.domain.check_domain_dns") as mock_dns:
        mock_dns.return_value = MagicMock(
            verified=True,
            cname_value="cname.reputation.motionlabs.kr",
            address_values=[],
            expected_cname="cname.reputation.motionlabs.kr",
            expected_addresses=[],
            verification_method="cname",
        )
        
        # Mock activation gate to pass
        with patch("app.api.admin.domain.evaluate_activation_gate") as mock_gate:
            mock_gate.return_value = {"ready": True}
            
            # 409 예외 발생 확인
            with pytest.raises(HTTPException) as exc_info:
                await verify_domain(hospital.id, db)
    
    assert exc_info.value.status_code == 409
    assert "HTTPS 인증서 발급이 이미 진행 중입니다" in exc_info.value.detail
    assert "경과 10분" in exc_info.value.detail or "경과 9분" in exc_info.value.detail
    
    # 병원 상태는 변경되지 않음
    assert hospital.domain_cert_job_state == DomainCertJobState.ISSUING.value
    assert hospital.domain_cert_job_started_at == started_10_min_ago


@pytest.mark.asyncio
async def test_cert_job_done_does_not_reprovision():
    """인증서가 이미 DONE 상태면 재발급하지 않음."""
    now = datetime.now(UTC)
    
    hospital = make_hospital(
        site_live=True,
        status=HospitalStatus.ACTIVE,
        cert_job_state=DomainCertJobState.DONE.value,
        cert_job_started_at=now - timedelta(hours=1),
        dns_verified_at=now - timedelta(hours=1),
    )
    db = FakeDB(hospital)
    
    # Mock DNS check to succeed
    with patch("app.api.admin.domain.check_domain_dns") as mock_dns:
        mock_dns.return_value = MagicMock(
            verified=True,
            cname_value="cname.reputation.motionlabs.kr",
            address_values=[],
            expected_cname="cname.reputation.motionlabs.kr",
            expected_addresses=[],
            verification_method="cname",
        )
        
        # Mock activation gate to pass
        with patch("app.api.admin.domain.evaluate_activation_gate") as mock_gate:
            mock_gate.return_value = {"ready": True}
            
            with patch.object(
                domain_api.provision_domain_certificate,
                "apply_async",
            ) as dispatch:
                result = await verify_domain(hospital.id, db)
                dispatch.assert_not_called()
    
    # 결과: DNS verified, cert ready
    assert result.dns_verified is True
    assert result.certificate_ready is True
    assert result.cert_job_state == DomainCertJobState.DONE.value


@pytest.mark.asyncio
async def test_cert_job_failed_allows_retry():
    """인증서 발급 FAILED 상태면 재시도 가능."""
    now = datetime.now(UTC)
    
    hospital = make_hospital(
        site_live=True,
        status=HospitalStatus.ACTIVE,
        cert_job_state=DomainCertJobState.FAILED.value,
        cert_job_started_at=now - timedelta(minutes=5),
        dns_verified_at=now - timedelta(minutes=5),
    )
    db = FakeDB(hospital)
    
    # Mock DNS check to succeed
    with patch("app.api.admin.domain.check_domain_dns") as mock_dns:
        mock_dns.return_value = MagicMock(
            verified=True,
            cname_value="cname.reputation.motionlabs.kr",
            address_values=[],
            expected_cname="cname.reputation.motionlabs.kr",
            expected_addresses=[],
            verification_method="cname",
        )
        
        # Mock activation gate to pass
        with patch("app.api.admin.domain.evaluate_activation_gate") as mock_gate:
            mock_gate.return_value = {"ready": True}
            
            with patch.object(
                domain_api.provision_domain_certificate,
                "apply_async",
            ) as dispatch:
                result = await verify_domain(hospital.id, db)
                dispatch.assert_called_once()

    assert hospital.domain_cert_job_state == DomainCertJobState.ISSUING.value
    assert result.certificate_ready is False


@pytest.mark.asyncio
async def test_dns_fail_does_not_activate():
    """DNS 검증 실패하면 site_live 전환 안 함."""
    hospital = make_hospital(site_live=False)
    db = FakeDB(hospital)
    
    # Mock DNS check to fail
    with patch("app.api.admin.domain.check_domain_dns") as mock_dns:
        mock_dns.return_value = MagicMock(
            verified=False,
            cname_value=None,
            address_values=[],
            expected_cname="cname.reputation.motionlabs.kr",
            expected_addresses=[],
            verification_method=None,
        )
        
        result = await verify_domain(hospital.id, db)
    
    # DNS 실패 → site_live=False 유지
    assert hospital.site_live is False
    assert getattr(hospital, "domain_cert_dns_verified_at", None) is None
    assert getattr(hospital, "domain_cert_job_state", None) is None
    
    assert result.dns_verified is False
    assert result.verified is False
    assert result.certificate_ready is False
    assert result.cert_job_state is None
    assert result.cert_job_started_at is None
    assert result.cert_job_elapsed_minutes is None


@pytest.mark.asyncio
async def test_elapsed_minutes_calculation():
    """DM-F1: 인증서 작업 경과 시간 계산."""
    now = datetime.now(UTC)
    started_25_min_ago = now - timedelta(minutes=25)
    
    hospital = make_hospital(
        site_live=True,
        status=HospitalStatus.ACTIVE,
        cert_job_state=DomainCertJobState.ISSUING.value,
        cert_job_started_at=started_25_min_ago,
        dns_verified_at=started_25_min_ago,
    )
    db = FakeDB(hospital)
    
    # Mock DNS check
    with patch("app.api.admin.domain.check_domain_dns") as mock_dns:
        mock_dns.return_value = MagicMock(verified=True, cname_value="test", address_values=[], expected_cname="test", expected_addresses=[], verification_method="cname")
        
        with patch("app.api.admin.domain.evaluate_activation_gate") as mock_gate:
            mock_gate.return_value = {"ready": True}
            
            # 409 예외에서 경과 시간 확인
            with pytest.raises(HTTPException) as exc_info:
                await verify_domain(hospital.id, db)
    
    # 경과 시간이 메시지에 포함되어야 함
    assert "경과 25분" in exc_info.value.detail or "경과 24분" in exc_info.value.detail
