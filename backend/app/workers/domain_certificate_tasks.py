"""Durable Certificate Manager work for token-bound custom-domain jobs."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TypedDict, assert_never

import anyio
from celery import Task, current_task
from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.database import get_async_sessionmaker
from app.models.hospital import Hospital
from app.services.domain_certificate_incidents import (
    CertificateFailureCause,
    open_certificate_failure_incident,
    recover_certificate_failure_incident,
)
from app.services.domain_certificate_jobs import (
    DomainCertificateJobIdentity,
    DomainCertificateTerminalState,
    domain_certificate_job_is_current,
    finish_domain_certificate_job,
)
from app.services.domain_certificate_manager import ensure_domain_certificate
from app.workers.dispatch_auth import require_dispatch

# GCP Certificate Manager의 LB 인증서는 DNS 전파 후 15~30분+ 걸리는 경우가 흔하다.
# 예산이 짧으면 성공했을 발급이 FAILED로 찍히고, 운영자가 재검증을 다시 눌러야 하는
# 확인 루프가 만들어진다. 처음 5분은 빠르게 확인하고(대부분 여기서 끝난다) 이후에는
# 1분 간격으로 기다린다 — 전체 예산 약 55분.
CERTIFICATE_MAX_POLLS = 60
CERTIFICATE_FAST_POLLS = 10
CERTIFICATE_FAST_POLL_SECONDS = 30
CERTIFICATE_SLOW_POLL_SECONDS = 60


def certificate_poll_countdown(retries: int) -> int:
    """다음 확인까지 기다릴 초. 초반에는 짧게, 이후에는 길게."""
    if retries < CERTIFICATE_FAST_POLLS:
        return CERTIFICATE_FAST_POLL_SECONDS
    return CERTIFICATE_SLOW_POLL_SECONDS


def certificate_poll_budget_seconds() -> int:
    """FAILED로 확정하기 전까지 기다리는 총 시간."""
    return sum(certificate_poll_countdown(retry) for retry in range(CERTIFICATE_MAX_POLLS))


class CertificateTaskOutcome(StrEnum):
    STALE = "STALE"
    DONE = "DONE"
    FAILED = "FAILED"
    PROVISIONING = "PROVISIONING"


class CertificateTaskResult(TypedDict):
    state: str


async def _hospital_name(db, hospital_id: uuid.UUID) -> str:
    return await db.scalar(select(Hospital.name).where(Hospital.id == hospital_id)) or "병원"


async def _finish_and_alert(
    db,
    identity: DomainCertificateJobIdentity,
    cause: CertificateFailureCause,
) -> None:
    """이 리스를 실제로 끝낸 실행만 알린다 — 중복 배달이 알림을 늘리지 않는다.

    상태 전이와 알림 큐잉을 같은 트랜잭션에 담아, FAILED로 찍혔는데 알림만 사라지는
    경우가 생기지 않게 한다.
    """
    finished = await finish_domain_certificate_job(
        db,
        identity,
        DomainCertificateTerminalState.FAILED,
    )
    if finished:
        await open_certificate_failure_incident(
            db,
            hospital_id=identity.hospital_id,
            hospital_name=await _hospital_name(db, identity.hospital_id),
            domain=identity.domain,
            cause=cause,
        )
    await db.commit()


async def _provision_claim(
    identity: DomainCertificateJobIdentity,
) -> CertificateTaskOutcome:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        if not await domain_certificate_job_is_current(db, identity):
            return CertificateTaskOutcome.STALE

        result = await anyio.to_thread.run_sync(ensure_domain_certificate, identity.domain)
        match result.phase:
            case "ACTIVE":
                await finish_domain_certificate_job(
                    db,
                    identity,
                    DomainCertificateTerminalState.DONE,
                )
                # 발급에 성공하면 열려 있던 인시던트만 닫는다 — 성공 Slack은 없다.
                await recover_certificate_failure_incident(
                    db,
                    hospital_id=identity.hospital_id,
                    domain=identity.domain,
                )
                await db.commit()
                return CertificateTaskOutcome.DONE
            case "PROVISIONING" | "NOT_FOUND":
                return CertificateTaskOutcome.PROVISIONING
            case "FAILED" if result.error_code == "CERTIFICATE_MANAGER_API":
                return CertificateTaskOutcome.PROVISIONING
            case "FAILED" | "INVALID" | "CONFIG_ERROR":
                await _finish_and_alert(db, identity, CertificateFailureCause.PROVIDER_REFUSED)
                return CertificateTaskOutcome.FAILED
            case unreachable:
                assert_never(unreachable)


async def _fail_exhausted_claim(identity: DomainCertificateJobIdentity) -> None:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        await _finish_and_alert(db, identity, CertificateFailureCause.BUDGET_EXHAUSTED)


@celery_app.task(
    bind=True,
    name="app.workers.domain_certificate_tasks.provision_domain_certificate",
    max_retries=CERTIFICATE_MAX_POLLS,
)
def provision_domain_certificate(
    self: Task,
    hospital_id: str,
    domain: str,
    token: str,
) -> CertificateTaskResult:
    """Provision one current claim and poll it without occupying an API request."""

    args = (hospital_id, domain, token)
    require_dispatch(
        current_task,
        "provision-domain-certificate",
        hospital_id,
        args=args,
    )
    identity = DomainCertificateJobIdentity(uuid.UUID(hospital_id), domain, token)
    retries = int(self.request.retries or 0)
    max_retries = int(self.max_retries or 0)
    if retries >= max_retries:
        anyio.run(_fail_exhausted_claim, identity)
        return {"state": CertificateTaskOutcome.FAILED.value}

    outcome = anyio.run(_provision_claim, identity)
    match outcome:
        case CertificateTaskOutcome.PROVISIONING:
            raise self.retry(countdown=certificate_poll_countdown(retries))
        case (
            CertificateTaskOutcome.STALE
            | CertificateTaskOutcome.DONE
            | CertificateTaskOutcome.FAILED
        ):
            return {"state": outcome.value}
        case unreachable:
            assert_never(unreachable)
