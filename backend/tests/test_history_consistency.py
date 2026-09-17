"""Historical contracts exercised against isolated SQLite, not production services."""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import arrow
import pytest
from sqlalchemy import func, select
from test_geo_autonomy_hardening import NOW, AsyncDB, heartbeat, seed
from test_geo_autonomy_hardening import db as db

from app.api.admin import content as content_api
from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.operations import Incident, OperationRun
from app.models.report import MonthlyReport
from app.models.sov import SovRecord
from app.services.fleet_heartbeat import collect_fleet_facts
from app.services.gap_driven_slots import GapTarget
from app.services.knowledge_changes import invalidate_source_authority
from app.workers.nightly_generation_batch import write_back_generated_content


def summary_tables(db):
    for model in (Incident, MonthlyReport, SovRecord):
        model.__table__.create(db.get_bind(), checkfirst=True)


async def test_schedule_save_keeps_prior_gap_targets_and_budget(db, monkeypatch):
    hospital, _ = seed(db)
    target_id = uuid.uuid4()
    existing = list(db.scalars(select(ContentItem)))
    existing[0].query_target_id = target_id
    db.commit()
    monkeypatch.setattr(content_api, "_schedule_readiness_blockers", AsyncMock(return_value=[]))
    monkeypatch.setattr(content_api.arrow, "now", lambda *args: arrow.get(NOW))
    monkeypatch.setattr(content_api, "build_gap_targets", lambda rows: [
        GapTarget(target_id, "지역 진료 안내", 0, 0, region_terms=("수원",))
    ])
    original = content_api.plan_gap_driven_slots
    observed = []

    def planning(slots, **kwargs):
        observed.extend(kwargs.get("existing", []))
        return original(slots, **kwargs)

    monkeypatch.setattr(content_api, "plan_gap_driven_slots", planning)
    result = await content_api.set_schedule(hospital.id, content_api.ScheduleCreate(
        plan="PLAN_12", publish_days=list(range(7)), active_from=NOW.date(),
    ), AsyncDB(db))
    assert len(observed) == 5 and observed[0].query_target_id == target_id
    assert result["slots_created"] == 7
    assert db.scalar(select(func.count()).select_from(ContentItem)) == 12
    assert db.scalar(select(func.count()).select_from(ContentItem).where(
        ContentItem.query_target_id == target_id,
    )) == 1


async def test_source_change_fences_unbound_inflight_generation(db):
    hospital, schedule = seed(db)
    source_id = uuid.uuid4()
    base = HospitalContentPhilosophy(hospital_id=hospital.id, version=1,
        status=PhilosophyStatus.APPROVED, is_base=True, source_asset_ids=[str(source_id)])
    old_token = uuid.uuid4()
    item = ContentItem(hospital_id=hospital.id, schedule_id=schedule.id,
        content_type="FAQ", sequence_no=6, total_count=12, scheduled_date=NOW.date(),
        status=ContentStatus.DRAFT, generation_claim_token=old_token,
        generation_claimed_at=NOW, content_philosophy_id=None)
    db.add_all([base, item])
    db.commit()
    old_revision = item.content_revision
    await invalidate_source_authority(AsyncDB(db), hospital.id, source_id,
                                      reason="SOURCE_EXCLUDED")
    db.commit()
    written = write_back_generated_content(db, item_id=item.id,
        expected_revision=old_revision, expected_claim_token=old_token,
        values={"body": "outdated provider output"})
    assert written == 0
    db.refresh(item)
    assert item.body is None and item.generation_claim_token is None
    assert item.content_revision == old_revision + 1
    assert db.scalar(select(func.count()).select_from(ContentItem).where(
        ContentItem.status == ContentStatus.PUBLISHED)) == 5


@pytest.mark.parametrize("verdict,mentioned,expected", [
    ("AMBIGUOUS", None, 0), (None, None, 0), ("NOT_MATCHED", False, 1),
    (None, False, 1), ("MATCHED", True, 1), ("AMBIGUOUS", False, 0),
])
def test_heartbeat_counts_only_confirmed_platform_answers(db, verdict, mentioned, expected):
    summary_tables(db)
    hospital, _ = seed(db)
    hospital.profile_complete = hospital.site_built = hospital.site_live = True
    db.add(SovRecord(hospital_id=hospital.id, query_id=uuid.uuid4(), ai_platform="chatgpt",
                    measured_at=NOW, measurement_status="SUCCESS", mention_verdict="MATCHED",
                    is_mentioned=True, raw_response="confirmed answer"))
    db.add(SovRecord(hospital_id=hospital.id, query_id=uuid.uuid4(), ai_platform="gemini",
                    measured_at=NOW, measurement_status="SUCCESS", mention_verdict=verdict,
                    is_mentioned=mentioned, raw_response="answer under review"))
    db.commit()
    facts = collect_fleet_facts(db, now=NOW)
    assert facts.hospitals == 1 and facts.measured_hospitals == expected


def test_known_intervention_is_not_hidden_by_missing_measurement():
    from app.services.fleet_heartbeat import FleetFacts
    text = heartbeat(facts=FleetFacts(2, 0, 1, 0, 0, 0)).message.fallback_text
    assert "담당자 확인 필요" in text.splitlines()[0]
    assert "미완료 작업 있음" in text.splitlines()[0]


@pytest.mark.parametrize("related,after,expected", [
    (True, True, 0), (False, True, 1), (True, False, 1),
])
def test_recovered_history_requires_actual_retry_lineage(db, related, after, expected):
    summary_tables(db)
    hospital, _ = seed(db)
    failed = OperationRun(hospital_id=hospital.id, operation_type="RUN_SOV", state="FAILED",
        total_count=1, failure_count=1, completed_at=NOW-timedelta(hours=1),
        updated_at=NOW-timedelta(hours=1), request_payload={"source_id": str(hospital.id)})
    db.add(failed)
    db.flush()
    succeeded = OperationRun(hospital_id=hospital.id, operation_type="RUN_SOV", state="SUCCEEDED",
        total_count=1, success_count=1, parent_run_id=failed.id if related else None,
        completed_at=NOW if after else NOW-timedelta(hours=2), updated_at=NOW,
        request_payload={"source_id": str(hospital.id)})
    db.add(succeeded)
    db.commit()
    facts = collect_fleet_facts(db, now=NOW)
    assert facts.failed_runs == 1
    assert facts.unresolved_failed_runs == expected


def test_recovered_failure_remains_history_not_current_alarm():
    from app.services.fleet_heartbeat import FleetFacts
    text = heartbeat(facts=FleetFacts(2, 0, 0, 1, 2, 0, 0)).message.fallback_text
    assert "점검 이상 없음" in text.splitlines()[0]
    assert "확인 필요 이슈 0건" in text
    assert "실패/부분 완료 이력" not in text


async def test_public_invalidation_acceptance_cannot_remain_pending(db, monkeypatch):
    from contextlib import asynccontextmanager

    from app.services import site_revalidation_control as control
    from app.services.public_surface_intents import enqueue_public_surface_intent

    summary_tables(db)
    hospital, _ = seed(db)
    run = enqueue_public_surface_intent(db, hospital)
    db.commit()
    adapter = AsyncDB(db)

    async def scalar(statement):
        return db.scalar(statement)

    adapter.scalar = scalar

    @asynccontextmanager
    async def session():
        yield adapter

    monkeypatch.setattr(control, "get_async_sessionmaker", lambda: session)
    assert await control.record_revalidation_success(run.id, 0) is True
    db.refresh(run)
    assert run.state == "SUCCEEDED"
    assert run.result_summary["invalidation_state"] == "ACCEPTED"
    assert run.result_summary["page_visibility_verified"] is False


# Regression: a newer authenticated redelivery must recover its existing reservation.
def test_redelivery_with_newer_run_claim_resumes_existing_reservation(db):
    from dataclasses import replace

    from test_geo_autonomy_hardening import make_execution

    from app.workers.generation_execution_claim import begin_generation_execution

    item, reservation, context, run = make_execution(db)
    assert begin_generation_execution(db, item.id, reservation, context, now=NOW) is not None
    # Model a legitimate redelivery after the previous process died: the durable
    # OperationRun is claimed at a newer version, while broker arguments are unchanged.
    run = db.get(OperationRun, run.id)
    run.version += 1
    run.lease_expires_at = NOW + timedelta(minutes=20)
    db.commit()
    newer = replace(context, version=run.version)
    resumed = begin_generation_execution(db, item.id, reservation, newer, now=NOW)
    assert resumed is not None
