from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, assert_never

from celery.result import AsyncResult
from fastapi import HTTPException
from kombu.exceptions import OperationalError as BrokerOperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.domain_verification_audit import audit_activation
from app.api.admin.domain_verification_responses import (
    dns_failure_response,
    dns_success_response,
)
from app.models.hospital import DomainCertJobState, DomainDnsStrategy, Hospital, HospitalStatus
from app.schemas.domain import DomainVerifyResponse
from app.services.audit_log import default_actor, write_audit_log
from app.services.domain_certificate_jobs import (
    CertificateJobClaimed,
    CertificateJobInFlight,
    CertificateJobReady,
    DomainCertificateClaimRequest,
    DomainCertificateHospitalMissing,
    DomainCertificateJobIdentity,
    DomainCertificateTerminalState,
    DomainChangedDuringVerification,
    claim_locked_domain_certificate_job,
    finish_domain_certificate_job,
    lock_hospital_for_domain_certificate,
)
from app.services.domain_dns import DomainDnsCheck, strategy_for_hospital
from app.services.hospital_lifecycle import (
    ActivationGateSnapshot,
    activation_gate_error,
)
from app.services.service_intervals import ServiceIntervalProvenance, open_service_interval
from app.workers.dispatch_auth import build_dispatch_headers

DnsChecker = Callable[[str, DomainDnsStrategy], Awaitable[DomainDnsCheck]]
ActivationGateEvaluator = Callable[
    [AsyncSession, Hospital], Awaitable[ActivationGateSnapshot]
]


class CertificateDispatchTask(Protocol):
    def apply_async(
        self,
        *,
        args: list[str],
        queue: str,
        headers: dict[str, str],
    ) -> AsyncResult: ...


@dataclass(frozen=True, slots=True)
class DomainVerificationDependencies:
    check_dns: DnsChecker
    evaluate_gate: ActivationGateEvaluator
    provision_task: CertificateDispatchTask


def elapsed_minutes(started_at: datetime | None, now: datetime) -> int | None:
    if started_at is None:
        return None
    return max(0, int((now - started_at).total_seconds() / 60))


async def verify_domain_for_hospital(
    hospital_id: uuid.UUID,
    db: AsyncSession,
    dependencies: DomainVerificationDependencies,
) -> DomainVerifyResponse:
    hospital = await _get_hospital_or_404(db, hospital_id)
    if not hospital.aeo_domain:
        raise HTTPException(
            status_code=400,
            detail="도메인이 설정되지 않았습니다. 먼저 도메인을 입력해 주세요.",
        )

    domain = hospital.aeo_domain
    dns_strategy = strategy_for_hospital(hospital)
    dns_check = await dependencies.check_dns(domain, dns_strategy)
    if not dns_check.verified:
        return dns_failure_response(
            domain,
            dns_strategy,
            dns_check,
        )

    now = datetime.now(UTC)
    request = DomainCertificateClaimRequest(hospital_id, domain, now)
    try:
        hospital = await lock_hospital_for_domain_certificate(db, request)
    except DomainCertificateHospitalMissing as exc:
        raise HTTPException(status_code=404, detail="병원을 찾을 수 없습니다.") from exc
    except DomainChangedDuringVerification as exc:
        raise HTTPException(
            status_code=409,
            detail="검증 중 도메인이 변경되었습니다. 화면을 새로고침한 뒤 다시 확인해 주세요.",
        ) from exc

    gate = await dependencies.evaluate_gate(db, hospital)
    if not gate["ready"]:
        raise HTTPException(status_code=409, detail=activation_gate_error(gate))

    previous_status = (
        hospital.status.value if hasattr(hospital.status, "value") else str(hospital.status)
    )
    previous_site_live = bool(hospital.site_live)
    if not hospital.site_live:
        hospital.site_live = True
        hospital.status = HospitalStatus.ACTIVE
        await open_service_interval(db, hospital.id, ServiceIntervalProvenance.ACTIVATION)

    job = claim_locked_domain_certificate_job(hospital, request)
    match job:
        case CertificateJobClaimed() | CertificateJobReady():
            pass
        case CertificateJobInFlight(started_at=started_at):
            await write_audit_log(
                db,
                action="verify_domain",
                hospital_id=hospital.id,
                actor=default_actor(),
                target_type="domain",
                target_id=domain,
                detail={
                    "dns_verified": True,
                    "cert_job_already_running": True,
                    "cert_job_elapsed_minutes": elapsed_minutes(started_at, now),
                },
            )
        case unreachable:
            assert_never(unreachable)

    if not previous_site_live:
        await audit_activation(
            db,
            hospital,
            domain,
            dns_check,
            previous_status,
            gate,
        )

    await db.commit()
    cert_job_state, cert_job_started_at = await _dispatch_or_describe_job(
        db,
        hospital,
        domain,
        job,
        now,
        dependencies.provision_task,
    )
    return dns_success_response(
        domain,
        dns_check,
        cert_job_state,
        cert_job_started_at,
        now,
        elapsed_minutes(cert_job_started_at, now),
    )


async def _dispatch_or_describe_job(
    db: AsyncSession,
    hospital: Hospital,
    domain: str,
    job: CertificateJobClaimed | CertificateJobInFlight | CertificateJobReady,
    now: datetime,
    provision_task: CertificateDispatchTask,
) -> tuple[str, datetime | None]:
    match job:
        case CertificateJobClaimed(token=token, started_at=started_at):
            identity = DomainCertificateJobIdentity(hospital.id, domain, token)
            try:
                provision_task.apply_async(
                    args=[str(hospital.id), domain, token],
                    queue="certificates",
                    headers=build_dispatch_headers(
                        "provision-domain-certificate",
                        str(hospital.id),
                    ),
                )
            except (BrokerOperationalError, OSError) as exc:
                await _record_dispatch_failure(db, hospital, identity)
                raise HTTPException(
                    status_code=503,
                    detail="DNS 확인은 완료됐지만 인증서 작업을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                ) from exc

            await _audit_dispatch(db, hospital, domain)
            await db.commit()
            return DomainCertJobState.ISSUING.value, started_at
        case CertificateJobInFlight(started_at=started_at):
            elapsed = elapsed_minutes(started_at, now)
            raise HTTPException(
                status_code=409,
                detail=(
                    "HTTPS 인증서 발급이 이미 진행 중입니다 "
                    f"(경과 {elapsed or 0}분). 작업이 완료될 때까지 기다려 주세요."
                ),
            )
        case CertificateJobReady(started_at=started_at):
            return DomainCertJobState.DONE.value, started_at
        case unreachable:
            assert_never(unreachable)


async def _record_dispatch_failure(
    db: AsyncSession,
    hospital: Hospital,
    identity: DomainCertificateJobIdentity,
) -> None:
    await finish_domain_certificate_job(
        db,
        identity,
        DomainCertificateTerminalState.FAILED,
    )
    await write_audit_log(
        db,
        action="provision_domain_certificate",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="domain",
        target_id=identity.domain,
        detail={
            "dns_verified": True,
            "certificate_ready": False,
            "certificate_phase": DomainCertJobState.FAILED.value,
            "queued": False,
            "certificate_error_code": "BROKER_UNAVAILABLE",
        },
    )
    await db.commit()


async def _audit_dispatch(db: AsyncSession, hospital: Hospital, domain: str) -> None:
    await write_audit_log(
        db,
        action="provision_domain_certificate",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="domain",
        target_id=domain,
        detail={
            "dns_verified": True,
            "certificate_ready": False,
            "certificate_phase": "QUEUED",
            "queued": True,
        },
    )


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital
