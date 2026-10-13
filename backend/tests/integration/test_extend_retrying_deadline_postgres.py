"""자동 재실행이 다시 미뤄지면 RETRYING 사고의 기한만 민다 — 없는 사고는 만들지 않는다(Phase 1-9a)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, IncidentSeverity
from app.services import ops_incident_alerts
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import open_or_touch_incident


def _bind(monkeypatch, db) -> None:
    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(ops_incident_alerts, "get_async_sessionmaker", lambda: _session)


async def _hospital(db) -> Hospital:
    hospital = Hospital(
        name="기한연장의원", slug=f"extend-{uuid.uuid4().hex[:10]}", status=HospitalStatus.ACTIVE
    )
    db.add(hospital)
    await db.flush()
    return hospital


async def test_a_retrying_v0_incident_gets_the_later_deadline(pg_async_session, monkeypatch):
    db = pg_async_session
    hospital = await _hospital(db)
    _bind(monkeypatch, db)
    first_deadline = datetime.now(UTC) + timedelta(hours=12)
    incident = await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="v0_report",
            object_type="hospital",
            object_id=str(hospital.id),
            fingerprint=IncidentFingerprint.UNKNOWN,
            incident_type="V0_REPORT_FAILED",
            severity=IncidentSeverity.HIGH,
            admin_path="/operations",
            customer_impact="초기 진단 리포트를 준비할 수 없습니다.",
            source_type="V0_REPORT",
            next_action="조치하지 마세요.",
            hospital_id=hospital.id,
            source_id=str(hospital.id),
            retry_until=first_deadline,
        ),
        actor="test",
        reason="test",
    )
    assert incident.state == "RETRYING"
    version = incident.version
    later = first_deadline + timedelta(hours=20)

    changed = await ops_incident_alerts.extend_retrying_deadline(
        pipeline="v0_report",
        object_type="hospital",
        object_id=str(hospital.id),
        fingerprint=IncidentFingerprint.UNKNOWN,
        retry_until=later,
    )

    stored = await db.scalar(
        select(Incident).where(Incident.id == incident.id).execution_options(populate_existing=True)
    )
    assert changed is True
    assert stored.state == "RETRYING"
    assert stored.sla_due_at == later
    assert stored.version == version + 1


async def test_no_incident_means_nothing_is_created(pg_async_session, monkeypatch):
    db = pg_async_session
    hospital = await _hospital(db)
    _bind(monkeypatch, db)
    key = build_incident_key("v0_report", "hospital", str(hospital.id), IncidentFingerprint.UNKNOWN)

    changed = await ops_incident_alerts.extend_retrying_deadline(
        pipeline="v0_report",
        object_type="hospital",
        object_id=str(hospital.id),
        fingerprint=IncidentFingerprint.UNKNOWN,
        retry_until=datetime.now(UTC) + timedelta(hours=6),
    )

    assert changed is False
    assert await db.scalar(select(func.count()).where(Incident.dedupe_key == key)) == 0
