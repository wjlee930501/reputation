"""Real PostgreSQL proof for durable custom-domain incident recovery."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.operations import Incident, IncidentState, NotificationOutbox, OperationRun
from app.services import domain_health_control as control

_ASYNC_URL = os.getenv(
    "TASK19_ASYNC_DATABASE_URL",
    "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test",
)


@pytest.mark.asyncio
async def test_acknowledged_domain_cause_is_not_reopened_or_repaged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = type(
        "AcknowledgedIncident",
        (),
        {
            "id": uuid.uuid4(),
            "state": IncidentState.ACKNOWLEDGED.value,
            "safe_error_code": "DOMAIN_UNHEALTHY",
            "episode_seq": 3,
        },
    )()

    class DB:
        async def scalar(self, _statement):
            return incident

    async def reject_reopen(*_args, **_kwargs):
        raise AssertionError("ACKNOWLEDGED domain episode must remain closed")

    monkeypatch.setattr(control, "open_or_touch_incident", reject_reopen)

    opened = await control._open_domain_incident(
        DB(),
        type("Hospital", (), {"id": uuid.uuid4(), "name": "테스트의원"})(),
        type("Run", (), {"id": uuid.uuid4(), "completed_at": datetime.now(UTC)})(),
        "example.test",
        "timeout",
    )

    assert opened is False
    assert incident.state == IncidentState.ACKNOWLEDGED.value
    assert incident.episode_seq == 3


@pytest.mark.asyncio
async def test_auto_recovery_closes_domain_incident_without_success_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hospital_id = uuid.uuid4()
    incident = Incident(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        state=IncidentState.OPEN.value,
        version=1,
        source_type="DOMAIN_HEALTH",
        source_id=f"{hospital_id}:example.test",
    )

    class DB:
        async def scalar(self, _statement):
            return incident

    async def mark_retrying(_db, _incident_id, **_kwargs):
        incident.state = IncidentState.RETRYING.value
        incident.version += 1
        return incident

    async def mark_recovered(_db, _incident_id, **_kwargs):
        incident.state = IncidentState.RECOVERED.value
        incident.version += 1
        return incident

    async def reject_notification(*_args, **_kwargs):
        raise AssertionError("automatic domain recovery must not enqueue Slack")

    monkeypatch.setattr(control, "mark_retrying", mark_retrying)
    monkeypatch.setattr(control, "mark_recovered", mark_recovered)
    monkeypatch.setattr(control, "enqueue_notification", reject_notification)

    recovered = await control._recover_domain_incident(
        DB(),
        type("Hospital", (), {"id": hospital_id, "name": "테스트의원"})(),
        type("Run", (), {"id": uuid.uuid4(), "completed_at": datetime.now(UTC)})(),
        "example.test",
    )

    assert recovered is True
    assert incident.state == IncidentState.RECOVERED.value


@pytest.mark.asyncio
async def test_wrong_marker_resets_streak_and_three_valid_checks_recover_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(_ASYNC_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    domain = f"task19-{hospital_id.hex[:10]}.example.com"
    start = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(control, "get_async_sessionmaker", lambda: sessions)

    try:
        async with sessions() as db:
            db.add(
                Hospital(
                    id=hospital_id,
                    name="Task19 도메인 테스트의원",
                    slug=f"task19-domain-{hospital_id.hex[:10]}",
                    status=HospitalStatus.ACTIVE,
                    plan=Plan.PLAN_12,
                    site_live=True,
                    aeo_domain=domain,
                )
            )
            await db.commit()

        first = await control.record_domain_health_check(
            hospital_id=hospital_id,
            canonical_host=domain,
            healthy=False,
            safe_reason="timeout",
            observed_at=start,
        )
        long_failure = await control.record_domain_health_check(
            hospital_id=hospital_id,
            canonical_host=domain,
            healthy=False,
            safe_reason="tls_or_network_error",
            observed_at=start + timedelta(hours=7),
        )
        assert first.incident_opened is True
        assert long_failure.incident_opened is False

        for offset in (timedelta(hours=7, minutes=15), timedelta(hours=7, minutes=30)):
            partial = await control.record_domain_health_check(
                hospital_id=hospital_id,
                canonical_host=domain,
                healthy=True,
                safe_reason="tenant_marker_ok",
                observed_at=start + offset,
            )
            assert partial.incident_recovered is False
        reset = await control.record_domain_health_check(
            hospital_id=hospital_id,
            canonical_host=domain,
            healthy=False,
            safe_reason="tenant_marker_mismatch",
            observed_at=start + timedelta(hours=7, minutes=45),
        )
        assert reset.healthy_streak == 0

        outcomes = []
        for minutes in (480, 495, 510):
            outcomes.append(
                await control.record_domain_health_check(
                    hospital_id=hospital_id,
                    canonical_host=domain,
                    healthy=True,
                    safe_reason="tenant_marker_ok",
                    observed_at=start + timedelta(minutes=minutes),
                )
            )
        assert [item.healthy_streak for item in outcomes] == [1, 2, 3]
        assert outcomes[-1].incident_recovered is True
        duplicate = await control.record_domain_health_check(
            hospital_id=hospital_id,
            canonical_host=domain,
            healthy=True,
            safe_reason="tenant_marker_ok",
            observed_at=start + timedelta(minutes=510),
        )
        assert duplicate.recorded is False
        assert duplicate.incident_recovered is False

        async with sessions() as db:
            incidents = list(
                (
                    await db.execute(select(Incident).where(Incident.hospital_id == hospital_id))
                ).scalars()
            )
            run_count = await db.scalar(
                select(func.count(OperationRun.id)).where(
                    OperationRun.hospital_id == hospital_id,
                    OperationRun.operation_type == "DOMAIN_HEALTH_CHECK",
                )
            )
            outbox_count = await db.scalar(
                select(func.count(NotificationOutbox.id)).where(
                    NotificationOutbox.hospital_id == hospital_id
                )
            )
            assert len(incidents) == 1
            assert incidents[0].state == IncidentState.RECOVERED.value
            assert incidents[0].occurrence_count == 3
            assert incidents[0].admin_path == f"/hospitals/{hospital_id}/onboarding"
            assert run_count == 8
            assert outbox_count == 1
    finally:
        async with sessions() as db:
            await db.execute(
                delete(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
            )
            await db.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
            await db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
            await db.execute(delete(Hospital).where(Hospital.id == hospital_id))
            await db.commit()
        await engine.dispose()
