"""기록 없는 병원에 측정을 보낸 코호트 공백은 호출자 기한까지 자동 복구다(Phase 1-7b)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident
from app.workers import monthly_cohort_incidents as cohort


def _bind(monkeypatch, db) -> None:
    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(cohort, "get_async_sessionmaker", lambda: _session)


async def _hospital(db) -> Hospital:
    hospital = Hospital(
        name="부트스트랩의원", slug=f"bootstrap-{uuid.uuid4().hex[:10]}", status=HospitalStatus.ACTIVE
    )
    db.add(hospital)
    await db.flush()
    return hospital


async def _incident(db, incident_id) -> Incident:
    return await db.scalar(
        select(Incident).where(Incident.id == incident_id).execution_options(populate_existing=True)
    )


async def test_bootstrapped_gap_is_retrying_until_the_caller_deadline(pg_async_session, monkeypatch):
    db = pg_async_session
    hospital = await _hospital(db)
    _bind(monkeypatch, db)
    deadline = datetime.now(UTC) + timedelta(hours=24)

    incident_id = await cohort.open_monthly_cohort_gap(
        hospital_id=hospital.id,
        hospital_name=hospital.name,
        period_key="2026-10",
        reason="no SovRecord",
        retrying_until=deadline,
    )

    incident = await _incident(db, incident_id)
    assert incident.state == "RETRYING"
    assert incident.sla_due_at == deadline
    assert incident.severity == "MEDIUM"


async def test_human_gap_and_a_passed_deadline_open_for_people(pg_async_session, monkeypatch):
    db = pg_async_session
    hospital = await _hospital(db)
    _bind(monkeypatch, db)

    human = await cohort.open_monthly_cohort_gap(
        hospital_id=hospital.id,
        hospital_name=hospital.name,
        period_key="2026-10",
        reason="not enough LOCAL ACTIVE targets",
    )
    stale = await cohort.open_monthly_cohort_gap(
        hospital_id=hospital.id,
        hospital_name=hospital.name,
        period_key="2026-11",
        reason="no SovRecord",
        retrying_until=datetime.now(UTC) - timedelta(minutes=1),
    )

    assert (await _incident(db, human)).state == "OPEN"
    stale_incident = await _incident(db, stale)
    assert stale_incident.state == "OPEN"
    # 지난 기한은 '측정을 보냈다'는 자동 복구 문구가 아니라 사람의 일 문구와 등급으로 연다.
    assert stale_incident.severity == "HIGH"
    assert "지역 질문" in stale_incident.next_action
