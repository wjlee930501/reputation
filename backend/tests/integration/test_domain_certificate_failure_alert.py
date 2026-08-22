"""인증서가 끝내 발급되지 않으면 사람에게 정확히 한 번 알린다.

배경: 예산을 소진하거나 공급자가 거부해 FAILED로 확정되면 커스텀 도메인은 https로
열리지 않는다 — 시스템이 더 할 수 있는 일이 없고 사람이 지금 DNS를 봐야 하는
상황인데, 여기에는 알림이 전혀 없었다. 반대로 발급 성공은 알리지 않는다(성공 Slack
금지). 재배달·재시도로 알림이 늘어나지도 않아야 한다.
"""

from __future__ import annotations

import importlib
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.hospital import DomainCertJobState, Hospital, HospitalStatus
from app.models.operations import Incident, IncidentState, NotificationOutbox
from app.services.domain_certificate_incidents import certificate_incident_object_id
from app.services.domain_certificate_manager import DomainCertificateResult

_DEFAULT_URL = "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"


def _worker():
    return importlib.import_module("app.workers.domain_certificate_tasks")


def _jobs():
    return importlib.import_module("app.services.domain_certificate_jobs")


def _async_url() -> str:
    raw = os.getenv("INTEGRATION_DATABASE_URL") or _DEFAULT_URL
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+asyncpg://" + raw[len(prefix) :]
    return raw


@pytest.fixture
async def certificate_alert_sessions(pg_engine):
    del pg_engine
    engine = create_async_engine(_async_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    domain = f"alert-{hospital_id.hex[:10]}.example.com"
    async with sessions() as db:
        db.add(
            Hospital(
                id=hospital_id,
                name="인증서알림의원",
                slug=f"cert-alert-{hospital_id.hex[:12]}",
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
            object_id = certificate_incident_object_id(hospital_id, domain)
            incident_ids = (
                await db.execute(select(Incident.id).where(Incident.source_id == object_id))
            ).scalars()
            await db.execute(
                delete(NotificationOutbox).where(
                    NotificationOutbox.incident_id.in_(list(incident_ids))
                )
            )
            await db.execute(delete(Incident).where(Incident.source_id == object_id))
            await db.execute(delete(Hospital).where(Hospital.id == hospital_id))
            await db.commit()
        await engine.dispose()


def _provider_result(domain: str, phase: str) -> DomainCertificateResult:
    return DomainCertificateResult(
        hostname=domain,
        ready=phase == "ACTIVE",
        phase=phase,
        certificate_state=phase,
        map_entry_state=phase,
        certificate_name="certificate",
        map_entry_name="entry",
        message="test",
    )


async def _claim(jobs, sessions, hospital_id, domain):
    async with sessions() as db:
        _, outcome = await jobs.claim_domain_certificate_job(
            db, jobs.DomainCertificateClaimRequest(hospital_id, domain)
        )
        await db.commit()
    return jobs.DomainCertificateJobIdentity(hospital_id, domain, outcome.token)


async def _notifications_for(sessions, hospital_id, domain) -> list[NotificationOutbox]:
    object_id = certificate_incident_object_id(hospital_id, domain)
    async with sessions() as db:
        incident_ids = list(
            (
                await db.execute(select(Incident.id).where(Incident.source_id == object_id))
            ).scalars()
        )
        if not incident_ids:
            return []
        return list(
            (
                await db.execute(
                    select(NotificationOutbox).where(
                        NotificationOutbox.incident_id.in_(incident_ids)
                    )
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_a_refused_certificate_alerts_once_even_across_redelivery(
    certificate_alert_sessions, monkeypatch
) -> None:
    jobs = _jobs()
    worker = _worker()
    sessions, hospital_id, domain = certificate_alert_sessions
    identity = await _claim(jobs, sessions, hospital_id, domain)
    monkeypatch.setattr(worker, "get_async_sessionmaker", lambda: sessions)
    monkeypatch.setattr(
        worker, "ensure_domain_certificate", lambda _d: _provider_result(domain, "FAILED")
    )

    first = await worker._provision_claim(identity)
    # 같은 리스가 다시 배달돼도 상태 전이는 한 번뿐이므로 알림도 늘지 않는다.
    second = await worker._provision_claim(identity)

    assert first == worker.CertificateTaskOutcome.FAILED
    assert second == worker.CertificateTaskOutcome.STALE
    notifications = await _notifications_for(sessions, hospital_id, domain)
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_an_exhausted_polling_budget_alerts_the_operator(
    certificate_alert_sessions, monkeypatch
) -> None:
    jobs = _jobs()
    worker = _worker()
    sessions, hospital_id, domain = certificate_alert_sessions
    identity = await _claim(jobs, sessions, hospital_id, domain)
    monkeypatch.setattr(worker, "get_async_sessionmaker", lambda: sessions)

    await worker._fail_exhausted_claim(identity)

    async with sessions() as db:
        hospital = await db.get(Hospital, hospital_id)
        assert hospital is not None
        assert hospital.domain_cert_job_state == DomainCertJobState.FAILED.value
    notifications = await _notifications_for(sessions, hospital_id, domain)
    assert len(notifications) == 1
    assert "https" in notifications[0].fallback_text.lower()


@pytest.mark.asyncio
async def test_a_successful_certificate_sends_no_notification(
    certificate_alert_sessions, monkeypatch
) -> None:
    jobs = _jobs()
    worker = _worker()
    sessions, hospital_id, domain = certificate_alert_sessions
    identity = await _claim(jobs, sessions, hospital_id, domain)
    monkeypatch.setattr(worker, "get_async_sessionmaker", lambda: sessions)
    monkeypatch.setattr(
        worker, "ensure_domain_certificate", lambda _d: _provider_result(domain, "ACTIVE")
    )

    outcome = await worker._provision_claim(identity)

    assert outcome == worker.CertificateTaskOutcome.DONE
    assert await _notifications_for(sessions, hospital_id, domain) == []


@pytest.mark.asyncio
async def test_a_later_success_closes_the_open_incident_without_a_new_alert(
    certificate_alert_sessions, monkeypatch
) -> None:
    jobs = _jobs()
    worker = _worker()
    sessions, hospital_id, domain = certificate_alert_sessions
    monkeypatch.setattr(worker, "get_async_sessionmaker", lambda: sessions)

    failing = await _claim(jobs, sessions, hospital_id, domain)
    monkeypatch.setattr(
        worker, "ensure_domain_certificate", lambda _d: _provider_result(domain, "FAILED")
    )
    await worker._provision_claim(failing)

    retried = await _claim(jobs, sessions, hospital_id, domain)
    monkeypatch.setattr(
        worker, "ensure_domain_certificate", lambda _d: _provider_result(domain, "ACTIVE")
    )
    await worker._provision_claim(retried)

    object_id = certificate_incident_object_id(hospital_id, domain)
    async with sessions() as db:
        incident = await db.scalar(select(Incident).where(Incident.source_id == object_id))
    assert incident is not None
    assert incident.state == IncidentState.RECOVERED.value
    # 실패 1건만 알린다 — 복구는 조용히 닫힌다.
    assert len(await _notifications_for(sessions, hospital_id, domain)) == 1
