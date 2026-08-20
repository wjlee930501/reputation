"""Test certificate job tracking and DNS-verified onboarding completion.

Tests for the domain onboarding step 5 fix:
- DM-F1: Certificate job state tracking (waiting/issuing/done/failed)
- DM-F2: In-flight certificate provision is idempotent (409 if duplicate)
- DM-F4: DNS verification success = operator-complete for step 5, cert is follow-up
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.admin.domain import verify_domain
from app.models.hospital import DomainCertJobState, Hospital, HospitalStatus
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
        # Mock service interval check - return None for new activation
        return None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True


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
    )
    hospital.domain_cert_job_state = cert_job_state
    hospital.domain_cert_job_started_at = cert_job_started_at
    hospital.domain_cert_dns_verified_at = dns_verified_at
    return hospital


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
            
            # Mock cert provisioning to return "issuing" state
            with patch("app.api.admin.domain.ensure_verified_domain_certificate") as mock_cert:
                mock_cert.return_value = DomainCertificateResult(
                    hostname="ai.testclinic.co.kr",
                    ready=False,
                    phase="PROVISIONING",
                    certificate_state="PROVISIONING",
                    map_entry_state="PENDING",
                    certificate_name="projects/test/certificates/cert-abc",
                    map_entry_name="projects/test/maps/map-def",
                    message="HTTPS 인증서를 준비하고 있습니다.",
                )
                
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
            
            # Mock cert manager NOT to be called (already DONE)
            with patch("app.api.admin.domain.ensure_verified_domain_certificate") as mock_cert:
                result = await verify_domain(hospital.id, db)
                
                # 인증서가 이미 DONE이므로 ensure_verified_domain_certificate 호출 안 함
                mock_cert.assert_not_called()
    
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
            
            # Mock cert provisioning to succeed this time
            with patch("app.api.admin.domain.ensure_verified_domain_certificate") as mock_cert:
                mock_cert.return_value = DomainCertificateResult(
                    hostname="ai.testclinic.co.kr",
                    ready=True,
                    phase="ACTIVE",
                    certificate_state="ACTIVE",
                    map_entry_state="ACTIVE",
                    certificate_name="projects/test/certificates/cert-abc",
                    map_entry_name="projects/test/maps/map-def",
                    message="HTTPS 인증서가 준비되었습니다.",
                )
                
                result = await verify_domain(hospital.id, db)
    
    # FAILED → DONE으로 전환
    assert hospital.domain_cert_job_state == DomainCertJobState.DONE.value
    assert result.certificate_ready is True


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
    assert hospital.domain_cert_dns_verified_at is None
    assert hospital.domain_cert_job_state is None
    
    assert result.dns_verified is False
    assert result.verified is False
    assert result.certificate_ready is False


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
