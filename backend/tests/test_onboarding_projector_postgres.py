from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.admin_user import AdminUser
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.monthly_control import HospitalServiceInterval
from app.services.notification_milestone_messages import (
    MilestoneBatch,
    enqueue_milestone_summary,
)
from app.workers.milestone_event_tasks import (
    canonical_projection_window,
    scan_onboarding_milestones,
)
from app.workers.milestone_onboarding_projection import observe_onboarding_milestones
from app.workers.milestone_projection_support import event_uuid

_DATABASE_URL = "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"
_PREFIX = "OPS-QA-T13-ONBOARDING"


@pytest.fixture
async def onboarding_sessions():
    engine = create_async_engine(_DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def cleanup() -> None:
        async with sessions() as db:
            await db.execute(
                text("DELETE FROM notification_outbox WHERE payload::text LIKE :pattern"),
                {"pattern": f"%{_PREFIX}%"},
            )
            await db.execute(
                text("DELETE FROM hospitals WHERE slug LIKE 'ops-qa-t13-onboarding-%'")
            )
            await db.execute(
                text("DELETE FROM admin_users WHERE email=:email"),
                {"email": "ops-qa-t13@example.invalid"},
            )
            await db.commit()

    await cleanup()
    try:
        yield sessions
    finally:
        await cleanup()
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_postgres_scan_keeps_only_highest_state_and_summary_dedupes(
    onboarding_sessions,
) -> None:
    window = canonical_projection_window(datetime(2026, 8, 10, 2, 47, tzinfo=UTC))
    owner_id = uuid.UUID("a1330000-0000-0000-0000-000000000001")
    async with onboarding_sessions() as db:
        db.add(
            AdminUser(
                id=owner_id,
                email="ops-qa-t13@example.invalid",
                name="QA AE",
                role="OPERATOR",
                password_hash="x",
            )
        )
        hospitals = [
            Hospital(
                name=f"{_PREFIX}-OVERDUE",
                slug="ops-qa-t13-onboarding-overdue",
                status=HospitalStatus.ONBOARDING,
            ),
            Hospital(
                name=f"{_PREFIX}-ACCEPTED",
                slug="ops-qa-t13-onboarding-accepted",
                status=HospitalStatus.ONBOARDING,
            ),
            Hospital(
                name=f"{_PREFIX}-READY",
                slug="ops-qa-t13-onboarding-ready",
                status=HospitalStatus.PENDING_DOMAIN,
                profile_complete=True,
                v0_report_done=True,
                site_built=True,
                schedule_set=True,
            ),
            Hospital(
                name=f"{_PREFIX}-ACTIVE",
                slug="ops-qa-t13-onboarding-active",
                status=HospitalStatus.ACTIVE,
                profile_complete=True,
                v0_report_done=True,
                site_built=True,
                schedule_set=True,
            ),
        ]
        db.add_all(hospitals)
        await db.flush()
        for index, hospital in enumerate(hospitals):
            accepted = index > 0
            occurred_at = window.start + timedelta(minutes=5 + index)
            handoff_id = uuid.UUID(f"a1330000-0000-0000-0000-{index + 10:012d}")
            db.add(
                HospitalHandoff(
                    id=handoff_id,
                    hospital_id=hospital.id,
                    state=HandoffState.HANDOFF_ACCEPTED if accepted else HandoffState.CONTRACTED,
                    sales_owner_id=owner_id,
                    ae_owner_id=owner_id,
                    contract_reference=f"QA-{index}",
                    contract_effective_at=window.start,
                    plan=Plan.PLAN_12,
                    sla_due_at=occurred_at if not accepted else window.start - timedelta(hours=1),
                    accepted_by_id=owner_id if accepted else None,
                    accepted_at=occurred_at if accepted else None,
                    acceptance_source=HandoffSource.DIRECT_CREATE,
                    created_at=window.start,
                    updated_at=occurred_at,
                )
            )
        db.add(
            HospitalServiceInterval(
                hospital_id=hospitals[-1].id,
                started_at=window.start + timedelta(minutes=9),
                provenance="ACTIVATION",
            )
        )
        await db.commit()

    async with onboarding_sessions() as db:
        projections = await scan_onboarding_milestones(db, window)
        observed = await observe_onboarding_milestones(db, window.end, {})
        batch = MilestoneBatch(projections, window.start, window.end)
        first = await enqueue_milestone_summary(db, batch, "http://localhost:3000")
        second = await enqueue_milestone_summary(db, batch, "http://localhost:3000")
        await db.commit()

    assert {item.kind.value for item in projections} == {
        "HANDOFF_OVERDUE",
        "HANDOFF_ACCEPTED",
        "ACTIVATION_READY",
        "HOSPITAL_ACTIVE",
    }
    accepted = next(item for item in projections if item.kind.value == "HANDOFF_ACCEPTED")
    expected_overdue_id = event_uuid(
        "HANDOFF_OVERDUE",
        uuid.UUID("a1330000-0000-0000-0000-000000000011"),
        window.start - timedelta(hours=1) + timedelta(microseconds=1),
    )
    assert accepted.recovery_of == f"milestone:v1:{expected_overdue_id}"
    assert first.id == second.id

    async with onboarding_sessions() as db:
        ready_hospital = await db.scalar(
            select(Hospital).where(Hospital.slug == "ops-qa-t13-onboarding-ready")
        )
        assert ready_hospital is not None
        ready_hospital.phone = "070-0000-0000"
        await db.commit()
    async with onboarding_sessions() as db:
        after_edit = await observe_onboarding_milestones(
            db, window.end + timedelta(minutes=15), observed.states
        )
        assert not any(
            item.hospital_id == ready_hospital.id and item.kind.value == "ACTIVATION_READY"
            for item in after_edit.milestones
        )

    async with onboarding_sessions() as db:
        count = await db.scalar(
            text("SELECT count(*) FROM notification_outbox WHERE payload::text LIKE :pattern"),
            {"pattern": f"%{_PREFIX}%"},
        )
        assert count == 1
