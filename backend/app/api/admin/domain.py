import uuid
from datetime import UTC, datetime
from typing import assert_never

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.domain_verification import (
    DomainVerificationDependencies,
    elapsed_minutes,
)
from app.api.admin.domain_verification import (
    verify_domain_for_hospital as run_domain_verification,
)
from app.core.config import settings
from app.core.database import get_db
from app.models.hospital import DomainCertJobState, DomainDnsStrategy, Hospital
from app.schemas.domain import DomainVerifyResponse
from app.services.domain_dns import (
    DomainDnsCheck,
    configured_custom_domain_ips,
    failure_message,
    normalize_dns_name,
    resolve_addresses,
    resolve_cname,
    strategy_for_hospital,
)
from app.services.domain_dns import (
    check_domain_dns as run_dns_check,
)
from app.services.hospital_lifecycle import evaluate_activation_gate
from app.workers.domain_certificate_tasks import provision_domain_certificate

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Domain"])

# Compatibility bindings used by other admin modules and focused tests.
_resolve_cname = resolve_cname
_resolve_addresses = resolve_addresses
_normalize_dns_name = normalize_dns_name
domain_dns_strategy_for_hospital = strategy_for_hospital
_failure_message = failure_message
_elapsed_minutes = elapsed_minutes


def _configured_custom_domain_ips() -> list[str]:
    return configured_custom_domain_ips(settings.CUSTOM_DOMAIN_IP_TARGETS)


async def check_domain_dns(
    domain: str,
    strategy: DomainDnsStrategy = DomainDnsStrategy.CNAME,
) -> DomainDnsCheck:
    """Delegate DNS policy while retaining live resolver patch points."""

    return await run_dns_check(
        domain,
        strategy,
        cname_resolver=_resolve_cname,
        address_resolver=_resolve_addresses,
        expected_cname=settings.CNAME_TARGET,
        custom_domain_ip_targets=settings.CUSTOM_DOMAIN_IP_TARGETS,
    )


@router.get("/{hospital_id}/domain/cert-status", response_model=DomainVerifyResponse)
async def check_cert_status(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DomainVerifyResponse:
    """Return committed worker state without calling Certificate Manager."""

    hospital = await _get_hospital_or_404(db, hospital_id)
    if not hospital.aeo_domain:
        raise HTTPException(status_code=400, detail="도메인이 설정되지 않았습니다.")

    domain = hospital.aeo_domain
    cert_job_state = hospital.domain_cert_job_state
    cert_job_started_at = hospital.domain_cert_job_started_at
    dns_verified_at = hospital.domain_cert_dns_verified_at
    now = datetime.now(UTC)
    if not dns_verified_at:
        return DomainVerifyResponse(
            domain=domain,
            verified=False,
            dns_verified=False,
            certificate_ready=False,
            certificate_phase=None,
            cert_job_state=cert_job_state,
            cert_job_started_at=cert_job_started_at,
            cert_job_elapsed_minutes=None,
            cname_value=None,
            expected_cname=settings.CNAME_TARGET,
            address_values=[],
            expected_addresses=[],
            verification_method=None,
            message="DNS 검증이 완료되지 않았습니다.",
        )

    elapsed = _elapsed_minutes(cert_job_started_at, now)
    try:
        state = DomainCertJobState(cert_job_state) if cert_job_state is not None else None
    except ValueError:
        state = None
    certificate_ready = state is DomainCertJobState.DONE
    match state:
        case DomainCertJobState.DONE:
            message = "DNS 확인 완료 · HTTPS 인증서 준비 완료"
        case DomainCertJobState.ISSUING:
            message = f"DNS 확인 완료 · HTTPS 인증서 발급 진행 중 (경과 {elapsed or 0}분)"
        case DomainCertJobState.FAILED:
            message = "DNS 확인 완료 · HTTPS 인증서 발급 실패. 재시도가 필요합니다."
        case None | DomainCertJobState.WAITING:
            message = "DNS 확인 완료"
        case unreachable:
            assert_never(unreachable)
    return DomainVerifyResponse(
        domain=domain,
        verified=True,
        dns_verified=True,
        certificate_ready=certificate_ready,
        certificate_phase=cert_job_state,
        cert_job_state=cert_job_state,
        cert_job_started_at=cert_job_started_at,
        cert_job_elapsed_minutes=elapsed,
        cname_value=None,
        expected_cname=settings.CNAME_TARGET,
        address_values=[],
        expected_addresses=[],
        verification_method="status_check",
        message=message,
    )


@router.post("/{hospital_id}/domain/verify", response_model=DomainVerifyResponse)
async def verify_domain(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DomainVerifyResponse:
    """Verify DNS, commit activation, and queue certificate work."""

    return await verify_domain_for_hospital(hospital_id, db)


async def verify_domain_for_hospital(
    hospital_id: uuid.UUID,
    db: AsyncSession,
) -> DomainVerifyResponse:
    """Canonical verification flow shared by domain and operations routes."""

    dependencies = DomainVerificationDependencies(
        check_dns=check_domain_dns,
        evaluate_gate=evaluate_activation_gate,
        provision_task=provision_domain_certificate,
    )
    return await run_domain_verification(hospital_id, db, dependencies)


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital
