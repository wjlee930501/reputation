"""Durable Certificate Manager work for token-bound custom-domain jobs."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TypedDict, assert_never

import anyio
from celery import Task, current_task

from app.core.celery_app import celery_app
from app.core.database import get_async_sessionmaker
from app.services.domain_certificate_jobs import (
    DomainCertificateJobIdentity,
    DomainCertificateTerminalState,
    domain_certificate_job_is_current,
    finish_domain_certificate_job,
)
from app.services.domain_certificate_manager import ensure_domain_certificate
from app.workers.dispatch_auth import require_dispatch


class CertificateTaskOutcome(StrEnum):
    STALE = "STALE"
    DONE = "DONE"
    FAILED = "FAILED"
    PROVISIONING = "PROVISIONING"


class CertificateTaskResult(TypedDict):
    state: str


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
                await db.commit()
                return CertificateTaskOutcome.DONE
            case "PROVISIONING" | "NOT_FOUND":
                return CertificateTaskOutcome.PROVISIONING
            case "FAILED" if result.error_code == "CERTIFICATE_MANAGER_API":
                return CertificateTaskOutcome.PROVISIONING
            case "FAILED" | "INVALID" | "CONFIG_ERROR":
                await finish_domain_certificate_job(
                    db,
                    identity,
                    DomainCertificateTerminalState.FAILED,
                )
                await db.commit()
                return CertificateTaskOutcome.FAILED
            case unreachable:
                assert_never(unreachable)


async def _fail_exhausted_claim(identity: DomainCertificateJobIdentity) -> None:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        await finish_domain_certificate_job(
            db,
            identity,
            DomainCertificateTerminalState.FAILED,
        )
        await db.commit()


@celery_app.task(
    bind=True,
    name="app.workers.domain_certificate_tasks.provision_domain_certificate",
    max_retries=20,
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
            raise self.retry(countdown=30)
        case (
            CertificateTaskOutcome.STALE
            | CertificateTaskOutcome.DONE
            | CertificateTaskOutcome.FAILED
        ):
            return {"state": outcome.value}
        case unreachable:
            assert_never(unreachable)
