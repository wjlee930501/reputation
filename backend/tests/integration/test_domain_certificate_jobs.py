from __future__ import annotations

import importlib
import importlib.util
import os
import uuid
from datetime import UTC, datetime

import anyio
import pytest
from fastapi import HTTPException
from kombu.exceptions import OperationalError as BrokerOperationalError
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.hospital import DomainCertJobState, Hospital, HospitalStatus
from app.models.operations import Incident, NotificationOutbox
from app.services.domain_certificate_manager import DomainCertificateResult

_DEFAULT_URL = "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"


def _jobs():
    spec = importlib.util.find_spec("app.services.domain_certificate_jobs")
    assert spec is not None, "domain certificate job claim service is missing"
    return importlib.import_module("app.services.domain_certificate_jobs")


def _worker():
    spec = importlib.util.find_spec("app.workers.domain_certificate_tasks")
    assert spec is not None, "domain certificate worker task is missing"
    return importlib.import_module("app.workers.domain_certificate_tasks")


def _async_url() -> str:
    raw = os.getenv("INTEGRATION_DATABASE_URL") or _DEFAULT_URL
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+asyncpg://" + raw[len(prefix) :]
    return raw


@pytest.fixture
async def certificate_job_sessions(pg_engine):
    del pg_engine
    engine = create_async_engine(_async_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    domain = f"claim-{hospital_id.hex[:10]}.example.com"
    async with sessions() as db:
        db.add(
            Hospital(
                id=hospital_id,
                name="인증서클레임의원",
                slug=f"cert-claim-{hospital_id.hex[:12]}",
                status=HospitalStatus.ACTIVE,
                profile_complete=True,
                v0_report_done=True,
                site_built=True,
                site_live=True,
                aeo_domain=domain,
                domain_cert_dns_verified_at=datetime.now(UTC),
            )
        )
        await db.commit()
    try:
        yield sessions, hospital_id, domain
    finally:
        async with sessions() as db:
            # 인시던트·발송 대기 행을 병원보다 먼저 지운다. 두 테이블의 hospital_id는
            # ON DELETE SET NULL이라 병원만 지우면 전역(hospital_id=NULL) 행으로 남고,
            # 뒤에 도는 테스트의 전역 outbox 배치가 그 행까지 집어 삼킨다.
            await db.execute(
                delete(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
            )
            await db.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
            await db.execute(delete(Hospital).where(Hospital.id == hospital_id))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_sessions_claim_one_certificate_job(certificate_job_sessions) -> None:
    jobs = _jobs()
    sessions, hospital_id, domain = certificate_job_sessions
    outcomes: list[object] = []

    async def claim() -> None:
        async with sessions() as db:
            _, outcome = await jobs.claim_domain_certificate_job(
                db, jobs.DomainCertificateClaimRequest(hospital_id, domain)
            )
            await db.commit()
            outcomes.append(outcome)

    async with anyio.create_task_group() as group:
        group.start_soon(claim)
        group.start_soon(claim)

    assert sum(isinstance(item, jobs.CertificateJobClaimed) for item in outcomes) == 1
    assert sum(isinstance(item, jobs.CertificateJobInFlight) for item in outcomes) == 1


@pytest.mark.asyncio
async def test_failed_job_can_be_claimed_again(certificate_job_sessions) -> None:
    jobs = _jobs()
    sessions, hospital_id, domain = certificate_job_sessions
    async with sessions() as db:
        await db.execute(
            update(Hospital)
            .where(Hospital.id == hospital_id)
            .values(domain_cert_job_state=DomainCertJobState.FAILED.value)
        )
        await db.commit()

    async with sessions() as db:
        hospital, outcome = await jobs.claim_domain_certificate_job(
            db, jobs.DomainCertificateClaimRequest(hospital_id, domain)
        )
        await db.commit()

    assert isinstance(outcome, jobs.CertificateJobClaimed)
    assert hospital.domain_cert_job_token == outcome.token
    assert hospital.domain_cert_job_domain == domain


@pytest.mark.asyncio
async def test_done_job_is_not_reclaimed(certificate_job_sessions) -> None:
    jobs = _jobs()
    sessions, hospital_id, domain = certificate_job_sessions
    async with sessions() as db:
        await db.execute(
            update(Hospital)
            .where(Hospital.id == hospital_id)
            .values(
                domain_cert_job_state=DomainCertJobState.DONE.value,
                domain_cert_job_domain=domain,
            )
        )
        await db.commit()

    async with sessions() as db:
        _, outcome = await jobs.claim_domain_certificate_job(
            db, jobs.DomainCertificateClaimRequest(hospital_id, domain)
        )

    assert isinstance(outcome, jobs.CertificateJobReady)


@pytest.mark.asyncio
async def test_old_domain_worker_cannot_finish_new_domain_job(certificate_job_sessions) -> None:
    jobs = _jobs()
    sessions, hospital_id, domain = certificate_job_sessions
    async with sessions() as db:
        _, outcome = await jobs.claim_domain_certificate_job(
            db, jobs.DomainCertificateClaimRequest(hospital_id, domain)
        )
        assert isinstance(outcome, jobs.CertificateJobClaimed)
        await db.commit()

    new_domain = f"new-{domain}"
    async with sessions() as db:
        await db.execute(
            update(Hospital)
            .where(Hospital.id == hospital_id)
            .values(
                aeo_domain=new_domain,
                domain_cert_job_state=None,
                domain_cert_job_started_at=None,
                domain_cert_job_token=None,
                domain_cert_job_domain=None,
            )
        )
        await db.commit()

    async with sessions() as db:
        finished = await jobs.finish_domain_certificate_job(
            db,
            jobs.DomainCertificateJobIdentity(hospital_id, domain, outcome.token),
            jobs.DomainCertificateTerminalState.DONE,
        )
        await db.commit()

    assert finished is False
    async with sessions() as db:
        hospital = await db.get(Hospital, hospital_id)
        assert hospital is not None
        assert hospital.aeo_domain == new_domain
        assert hospital.domain_cert_job_state is None


async def _claim_identity(jobs, sessions, hospital_id, domain):
    async with sessions() as db:
        _, outcome = await jobs.claim_domain_certificate_job(
            db, jobs.DomainCertificateClaimRequest(hospital_id, domain)
        )
        assert isinstance(outcome, jobs.CertificateJobClaimed)
        await db.commit()
    return jobs.DomainCertificateJobIdentity(hospital_id, domain, outcome.token)


@pytest.mark.asyncio
async def test_worker_skips_provider_for_stale_claim(
    certificate_job_sessions, monkeypatch
) -> None:
    jobs = _jobs()
    worker = _worker()
    sessions, hospital_id, domain = certificate_job_sessions
    identity = await _claim_identity(jobs, sessions, hospital_id, domain)
    async with sessions() as db:
        await db.execute(
            update(Hospital)
            .where(Hospital.id == hospital_id)
            .values(aeo_domain=f"new-{domain}", domain_cert_job_token=None)
        )
        await db.commit()

    def must_not_provision(_domain: str) -> DomainCertificateResult:
        raise AssertionError("a stale domain claim must not reach Certificate Manager")

    monkeypatch.setattr(worker, "get_async_sessionmaker", lambda: sessions)
    monkeypatch.setattr(worker, "ensure_domain_certificate", must_not_provision)

    outcome = await worker._provision_claim(identity)

    assert outcome == worker.CertificateTaskOutcome.STALE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_phase", "ready", "expected_outcome", "expected_state"),
    [
        ("ACTIVE", True, "DONE", DomainCertJobState.DONE.value),
        ("FAILED", False, "FAILED", DomainCertJobState.FAILED.value),
        ("PROVISIONING", False, "PROVISIONING", DomainCertJobState.ISSUING.value),
    ],
)
async def test_worker_maps_provider_phase_to_token_bound_state(
    certificate_job_sessions,
    monkeypatch,
    provider_phase: str,
    ready: bool,
    expected_outcome: str,
    expected_state: str,
) -> None:
    jobs = _jobs()
    worker = _worker()
    sessions, hospital_id, domain = certificate_job_sessions
    identity = await _claim_identity(jobs, sessions, hospital_id, domain)
    provider_result = DomainCertificateResult(
        hostname=domain,
        ready=ready,
        phase=provider_phase,
        certificate_state=provider_phase,
        map_entry_state=provider_phase,
        certificate_name="certificate",
        map_entry_name="entry",
        message="test",
    )
    monkeypatch.setattr(worker, "get_async_sessionmaker", lambda: sessions)
    monkeypatch.setattr(worker, "ensure_domain_certificate", lambda _domain: provider_result)

    outcome = await worker._provision_claim(identity)

    assert outcome.value == expected_outcome
    async with sessions() as db:
        hospital = await db.get(Hospital, hospital_id)
        assert hospital is not None
        assert hospital.domain_cert_job_state == expected_state


class RecordingCertificateTask:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def apply_async(self, *, args, queue, headers):
        self.calls.append({"args": args, "queue": queue, "headers": headers})
        if self.fail:
            raise BrokerOperationalError("broker unavailable")


def _successful_dns():
    from app.api.admin.domain import DomainDnsCheck

    return DomainDnsCheck(
        cname_value="cname.reputation.motionlabs.kr",
        address_values=[],
        expected_cname="cname.reputation.motionlabs.kr",
        expected_addresses=[],
        verified=True,
        verification_method="cname",
    )


@pytest.mark.asyncio
async def test_concurrent_verify_requests_dispatch_one_certificate_job(
    certificate_job_sessions,
    monkeypatch,
) -> None:
    from app.api.admin import domain as domain_api

    sessions, hospital_id, domain = certificate_job_sessions
    task = RecordingCertificateTask()
    provider_calls = 0

    async def successful_dns(*_args):
        return _successful_dns()

    async def legacy_provider(_domain: str):
        nonlocal provider_calls
        provider_calls += 1
        await anyio.sleep(0)
        return DomainCertificateResult(
            hostname=domain,
            ready=False,
            phase="PROVISIONING",
            certificate_state="PROVISIONING",
            map_entry_state="PENDING",
            certificate_name="certificate",
            map_entry_name="entry",
            message="test",
        )

    monkeypatch.setattr(domain_api, "check_domain_dns", successful_dns)
    monkeypatch.setattr(
        domain_api,
        "ensure_verified_domain_certificate",
        legacy_provider,
        raising=False,
    )
    monkeypatch.setattr(domain_api, "provision_domain_certificate", task, raising=False)
    statuses: list[int] = []

    async def verify() -> None:
        async with sessions() as db:
            try:
                await domain_api.verify_domain(hospital_id, db)
            except HTTPException as exc:
                statuses.append(exc.status_code)
            else:
                statuses.append(200)

    async with anyio.create_task_group() as group:
        group.start_soon(verify)
        group.start_soon(verify)

    assert sorted(statuses) == [200, 409]
    assert provider_calls == 0
    assert len(task.calls) == 1
    assert task.calls[0]["queue"] == "certificates"


@pytest.mark.asyncio
async def test_broker_failure_marks_exact_claim_failed(
    certificate_job_sessions,
    monkeypatch,
) -> None:
    from app.api.admin import domain as domain_api

    sessions, hospital_id, _domain = certificate_job_sessions
    task = RecordingCertificateTask(fail=True)

    async def successful_dns(*_args):
        return _successful_dns()

    monkeypatch.setattr(domain_api, "check_domain_dns", successful_dns)
    monkeypatch.setattr(domain_api, "provision_domain_certificate", task, raising=False)

    async with sessions() as db:
        with pytest.raises(HTTPException) as exc_info:
            await domain_api.verify_domain(hospital_id, db)

    assert exc_info.value.status_code == 503
    async with sessions() as db:
        hospital = await db.get(Hospital, hospital_id)
        assert hospital is not None
        assert hospital.domain_cert_job_state == DomainCertJobState.FAILED.value
        assert hospital.domain_cert_job_token is None
