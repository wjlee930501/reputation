"""Token-bound claims for custom-domain certificate provisioning."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import assert_never

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hospital import DomainCertJobState, Hospital

# ISSUING 클레임은 이 시간이 지나면 만료로 본다. 커밋 직후 디스패치 전에 워커가
# 죽으면 아무도 폴링하지 않는 ISSUING이 남고, 만료가 없으면 재검증이 영구히 409로
# 막혀 운영자가 도메인을 되살릴 방법이 없다. 만료된 클레임을 다시 잡아도 GCP
# 리소스 id는 결정적이라 발급 작업이 중복되지 않는다.
CERTIFICATE_LEASE_MINUTES = 30


@dataclass(frozen=True, slots=True)
class DomainCertificateClaimRequest:
    hospital_id: uuid.UUID
    expected_domain: str
    verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DomainCertificateJobIdentity:
    hospital_id: uuid.UUID
    domain: str
    token: str


@dataclass(frozen=True, slots=True)
class CertificateJobClaimed:
    domain: str
    token: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class CertificateJobInFlight:
    domain: str
    token: str
    started_at: datetime | None


@dataclass(frozen=True, slots=True)
class CertificateJobReady:
    domain: str
    started_at: datetime | None


CertificateJobClaim = CertificateJobClaimed | CertificateJobInFlight | CertificateJobReady


class DomainCertificateTerminalState(StrEnum):
    DONE = DomainCertJobState.DONE.value
    FAILED = DomainCertJobState.FAILED.value


@dataclass(frozen=True, slots=True)
class DomainCertificateHospitalMissing(Exception):
    hospital_id: uuid.UUID

    def __str__(self) -> str:
        return f"hospital {self.hospital_id} not found"


@dataclass(frozen=True, slots=True)
class DomainChangedDuringVerification(Exception):
    expected_domain: str
    current_domain: str | None

    def __str__(self) -> str:
        return (
            f"domain changed during verification: expected={self.expected_domain} "
            f"current={self.current_domain}"
        )


@dataclass(frozen=True, slots=True)
class DomainCertificateStateInvalid(Exception):
    state: str

    def __str__(self) -> str:
        return f"invalid domain certificate job state: {self.state}"


async def claim_domain_certificate_job(
    db: AsyncSession,
    request: DomainCertificateClaimRequest,
) -> tuple[Hospital, CertificateJobClaim]:
    """Lock one hospital row and claim its current domain exactly once."""

    hospital = await lock_hospital_for_domain_certificate(db, request)
    return hospital, claim_locked_domain_certificate_job(hospital, request)


async def lock_hospital_for_domain_certificate(
    db: AsyncSession,
    request: DomainCertificateClaimRequest,
) -> Hospital:
    """Return the current domain row under a transaction-scoped write lock."""

    hospital = await db.scalar(
        select(Hospital)
        .where(Hospital.id == request.hospital_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if hospital is None:
        raise DomainCertificateHospitalMissing(request.hospital_id)
    if hospital.aeo_domain != request.expected_domain:
        raise DomainChangedDuringVerification(request.expected_domain, hospital.aeo_domain)
    return hospital


def claim_locked_domain_certificate_job(
    hospital: Hospital,
    request: DomainCertificateClaimRequest,
) -> CertificateJobClaim:
    """Claim a row already locked by ``lock_hospital_for_domain_certificate``."""

    now = request.verified_at or datetime.now(UTC)
    if hospital.domain_cert_dns_verified_at is None:
        hospital.domain_cert_dns_verified_at = now

    raw_state = hospital.domain_cert_job_state
    if raw_state is None:
        state = None
    else:
        try:
            state = DomainCertJobState(raw_state)
        except ValueError as exc:
            raise DomainCertificateStateInvalid(raw_state) from exc
    job_domain = hospital.domain_cert_job_domain
    token = hospital.domain_cert_job_token
    match state:
        case DomainCertJobState.DONE if job_domain in {None, request.expected_domain}:
            return CertificateJobReady(
                request.expected_domain,
                hospital.domain_cert_job_started_at,
            )
        case DomainCertJobState.ISSUING if (
            job_domain == request.expected_domain
            and token is not None
            and not certificate_lease_expired(hospital.domain_cert_job_started_at, now)
        ):
            return CertificateJobInFlight(
                request.expected_domain,
                token,
                hospital.domain_cert_job_started_at,
            )
        case (
            None
            | DomainCertJobState.WAITING
            | DomainCertJobState.FAILED
            | DomainCertJobState.ISSUING
            | DomainCertJobState.DONE
        ):
            return _claim(hospital, request.expected_domain, now)
        case unreachable:
            assert_never(unreachable)


def certificate_lease_expired(started_at: datetime | None, now: datetime) -> bool:
    """진행 중이라고 주장하는 클레임이 실제로는 죽은 채 남아 있는지.

    시작 시각이 없는 행(이전 버전이 남긴 흔적)도 만료로 본다 — 진행 중임을 증명할
    근거가 없는데 재검증을 막을 이유는 없다.
    """
    if started_at is None:
        return True
    reference = started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)
    return now - reference >= timedelta(minutes=CERTIFICATE_LEASE_MINUTES)


def _claim(hospital: Hospital, domain: str, started_at: datetime) -> CertificateJobClaimed:
    token = str(uuid.uuid4())
    hospital.domain_cert_job_state = DomainCertJobState.ISSUING.value
    hospital.domain_cert_job_started_at = started_at
    hospital.domain_cert_job_token = token
    hospital.domain_cert_job_domain = domain
    return CertificateJobClaimed(domain, token, started_at)


async def domain_certificate_job_is_current(
    db: AsyncSession,
    identity: DomainCertificateJobIdentity,
) -> bool:
    current = await db.scalar(
        select(Hospital.id).where(
            Hospital.id == identity.hospital_id,
            Hospital.aeo_domain == identity.domain,
            Hospital.domain_cert_job_domain == identity.domain,
            Hospital.domain_cert_job_token == identity.token,
            Hospital.domain_cert_job_state == DomainCertJobState.ISSUING.value,
        )
    )
    return current is not None


async def finish_domain_certificate_job(
    db: AsyncSession,
    identity: DomainCertificateJobIdentity,
    terminal_state: DomainCertificateTerminalState,
) -> bool:
    """Finish only the exact lease; stale workers become harmless no-ops."""

    result = await db.execute(
        update(Hospital)
        .where(
            Hospital.id == identity.hospital_id,
            Hospital.aeo_domain == identity.domain,
            Hospital.domain_cert_job_domain == identity.domain,
            Hospital.domain_cert_job_token == identity.token,
            Hospital.domain_cert_job_state == DomainCertJobState.ISSUING.value,
        )
        .values(
            domain_cert_job_state=terminal_state.value,
            domain_cert_job_token=None,
        )
        .returning(Hospital.id)
    )
    return result.scalar_one_or_none() is not None
