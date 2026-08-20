from datetime import datetime

from app.models.hospital import DomainCertJobState, DomainDnsStrategy
from app.schemas.domain import DomainVerifyResponse
from app.services.domain_dns import DomainDnsCheck, failure_message


def dns_failure_response(
    domain: str,
    strategy: DomainDnsStrategy,
    dns_check: DomainDnsCheck,
) -> DomainVerifyResponse:
    return DomainVerifyResponse(
        domain=domain,
        verified=False,
        dns_verified=False,
        certificate_ready=False,
        certificate_phase=None,
        cert_job_state=None,
        cert_job_started_at=None,
        cert_job_elapsed_minutes=None,
        cname_value=dns_check.cname_value,
        expected_cname=dns_check.expected_cname,
        address_values=dns_check.address_values,
        expected_addresses=dns_check.expected_addresses,
        verification_method=dns_check.verification_method,
        message=failure_message(domain, strategy, dns_check),
    )


def dns_success_response(
    domain: str,
    dns_check: DomainDnsCheck,
    cert_job_state: str,
    cert_job_started_at: datetime | None,
    now: datetime,
    elapsed: int | None,
) -> DomainVerifyResponse:
    certificate_ready = cert_job_state == DomainCertJobState.DONE.value
    resolved_value = dns_check.cname_value or ", ".join(dns_check.address_values)
    if certificate_ready:
        message = f"DNS 확인 완료 · HTTPS 인증서 준비 완료 ({domain} → {resolved_value})"
    elif cert_job_state == DomainCertJobState.ISSUING.value:
        message = (
            "DNS 확인 완료 · HTTPS 인증서 발급 진행 중 "
            f"(경과 {elapsed or 0}분). 일반적으로 수 분 내에 완료됩니다."
        )
    else:
        message = (
            f"DNS 확인 완료 ({domain} → {resolved_value}). 운영 전환은 완료되었으며, "
            "HTTPS 인증서는 백그라운드에서 발급됩니다."
        )
    return DomainVerifyResponse(
        domain=domain,
        verified=True,
        dns_verified=True,
        certificate_ready=certificate_ready,
        certificate_phase=cert_job_state,
        cert_job_state=cert_job_state,
        cert_job_started_at=cert_job_started_at,
        cert_job_elapsed_minutes=elapsed,
        cname_value=dns_check.cname_value,
        expected_cname=dns_check.expected_cname,
        address_values=dns_check.address_values,
        expected_addresses=dns_check.expected_addresses,
        verification_method=dns_check.verification_method,
        message=message,
    )
