"""인증서 폴링 예산과 ISSUING 리스 만료 — 순수 규칙만 고정한다.

배경: 20회 × 30초(≈10분) 뒤 FAILED로 확정하던 예산은 GCP Certificate Manager의
실제 발급 시간(DNS 전파 후 15~30분+)보다 짧다. 성공했을 작업이 FAILED로 찍히면
운영자가 재검증을 다시 눌러야 하는 확인 루프가 생긴다. 반대로 ISSUING 클레임에
만료가 없으면, 커밋 직후 디스패치 전에 죽은 작업이 재검증을 영구히 409로 막는다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.hospital import DomainCertJobState, Hospital
from app.services.domain_certificate_jobs import (
    CERTIFICATE_LEASE_MINUTES,
    CertificateJobClaimed,
    CertificateJobInFlight,
    DomainCertificateClaimRequest,
    certificate_lease_expired,
    claim_locked_domain_certificate_job,
)
from app.workers.domain_certificate_tasks import (
    CERTIFICATE_MAX_POLLS,
    certificate_poll_budget_seconds,
    certificate_poll_countdown,
)

DOMAIN = "clinic.example.com"


def _issuing_hospital(started_at: datetime | None) -> Hospital:
    hospital = Hospital(
        id=uuid.uuid4(),
        name="인증서예산의원",
        slug="cert-budget",
        aeo_domain=DOMAIN,
    )
    hospital.domain_cert_job_state = DomainCertJobState.ISSUING.value
    hospital.domain_cert_job_started_at = started_at
    hospital.domain_cert_job_token = str(uuid.uuid4())
    hospital.domain_cert_job_domain = DOMAIN
    hospital.domain_cert_dns_verified_at = started_at
    return hospital


def test_the_polling_budget_outlasts_a_normal_gcp_issuance():
    budget_minutes = certificate_poll_budget_seconds() / 60

    assert budget_minutes >= 40, "실제 발급 시간보다 짧은 예산은 false FAILED를 만든다"


def test_early_polls_are_quick_and_later_polls_back_off():
    assert certificate_poll_countdown(0) == 30
    assert certificate_poll_countdown(CERTIFICATE_MAX_POLLS - 1) == 60
    # 간격은 줄어들지 않는다 — 늦게 확인할수록 더 기다린다.
    intervals = [certificate_poll_countdown(retry) for retry in range(CERTIFICATE_MAX_POLLS)]
    assert intervals == sorted(intervals)


def test_a_live_claim_still_blocks_a_duplicate_run():
    now = datetime.now(UTC)
    hospital = _issuing_hospital(now - timedelta(minutes=CERTIFICATE_LEASE_MINUTES - 1))

    outcome = claim_locked_domain_certificate_job(
        hospital, DomainCertificateClaimRequest(hospital.id, DOMAIN, now)
    )

    assert isinstance(outcome, CertificateJobInFlight)


def test_an_expired_claim_is_taken_over_instead_of_blocking_forever():
    """커밋 후 디스패치 전에 죽으면 아무도 폴링하지 않는 ISSUING이 남는다."""
    now = datetime.now(UTC)
    hospital = _issuing_hospital(now - timedelta(minutes=CERTIFICATE_LEASE_MINUTES + 1))
    stale_token = hospital.domain_cert_job_token

    outcome = claim_locked_domain_certificate_job(
        hospital, DomainCertificateClaimRequest(hospital.id, DOMAIN, now)
    )

    assert isinstance(outcome, CertificateJobClaimed)
    assert outcome.token != stale_token
    # 새 토큰이 유일한 현재 리스가 되므로, 살아 있던 옛 워커는 다음 확인에서 멈춘다.
    assert hospital.domain_cert_job_token == outcome.token
    assert hospital.domain_cert_job_started_at == now


def test_a_claim_without_a_start_time_cannot_block_recovery():
    now = datetime.now(UTC)
    hospital = _issuing_hospital(None)
    hospital.domain_cert_dns_verified_at = now

    outcome = claim_locked_domain_certificate_job(
        hospital, DomainCertificateClaimRequest(hospital.id, DOMAIN, now)
    )

    assert isinstance(outcome, CertificateJobClaimed)


@pytest.mark.parametrize("naive", [True, False])
def test_lease_expiry_handles_stored_timestamps_with_or_without_a_timezone(naive: bool):
    now = datetime.now(UTC)
    started = now - timedelta(minutes=CERTIFICATE_LEASE_MINUTES + 5)
    if naive:
        started = started.replace(tzinfo=None)

    assert certificate_lease_expired(started, now) is True
    assert certificate_lease_expired(now, now) is False
