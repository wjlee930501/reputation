"""Offline behavior proofs with isolated SQLite; no providers or external DB."""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import arrow
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.admin import content as api
from app.api.admin import director_feedback as feedback_api
from app.api.admin import handoffs
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.director_delta import DirectorDelta
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.operations import NotificationOutbox, OperationRun
from app.models.sov import AIQueryTarget, ExposureGap
from app.services.audit_log import reset_request_actor, set_request_actor
from app.services.fleet_heartbeat import FleetFacts, build_fleet_heartbeat
from app.services.notification_contracts import validate_message
from app.services.notification_store import enqueue_notification_sync
from app.services.operator_action import requires_operator_action
from app.services.pipeline_watchdog import WatchdogReport
from app.workers.monthly_slots import create_next_month_slots_for_schedule

NOW = datetime(2026, 9, 15, 9, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for model in (
        Hospital,
        ContentSchedule,
        ContentItem,
        AdminAuditLog,
        DirectorDelta,
        HospitalContentPhilosophy,
        NotificationOutbox,
        OperationRun,
        AIQueryTarget,
        ExposureGap,
    ):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


class AsyncDB:
    def __init__(self, db):
        self.db = db

    def get_bind(self):
        return self.db.get_bind()

    def add(self, row):
        self.db.add(row)

    async def execute(self, stmt):
        return self.db.execute(stmt)

    async def get(self, model, key):
        return self.db.get(model, key)

    async def flush(self):
        self.db.flush()

    async def commit(self):
        self.db.commit()

    async def refresh(self, row):
        self.db.refresh(row)

    async def run_sync(self, fn, *args):
        return fn(self.db, *args)


def seed(db):
    hospital = Hospital(
        name="Offline",
        slug=str(uuid.uuid4()),
        plan=Plan.PLAN_12,
        status=HospitalStatus.ACTIVE,
        site_live=False,
        schedule_set=True,
    )
    db.add(hospital)
    db.flush()
    schedule = ContentSchedule(
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=list(range(7)),
        active_from=date(2026, 9, 1),
        is_active=True,
    )
    db.add(schedule)
    db.flush()
    for number in range(1, 6):
        db.add(
            ContentItem(
                hospital_id=hospital.id,
                schedule_id=schedule.id,
                sequence_no=number,
                total_count=12,
                content_type=ContentType.FAQ,
                scheduled_date=date(2026, 9, number),
                status=ContentStatus.PUBLISHED,
                first_published_at=NOW,
                published_at=NOW,
                published_by="doctor",
                body="preserved",
            )
        )
    db.commit()
    return hospital, schedule


async def test_publish_reject_reset_and_repair_preserve_twelve_identities(db, monkeypatch):
    hospital, old = seed(db)
    adapter = AsyncDB(db)
    monkeypatch.setattr(api, "_schedule_readiness_blockers", AsyncMock(return_value=[]))
    monkeypatch.setattr(api.arrow, "now", lambda *args: arrow.get(NOW).to("Asia/Seoul"))
    monkeypatch.setattr(
        api.regenerate_content_item, "apply_async", lambda **kwargs: pytest.fail("dispatch")
    )
    item = db.scalars(select(ContentItem).order_by(ContentItem.sequence_no)).first()
    original_id, first = item.id, item.first_published_at
    token = set_request_actor("doctor@example.com")
    try:
        await api.reject_content(
            hospital.id, item.id, api.RejectBody(reason="수정 요청입니다"), adapter
        )
    finally:
        reset_request_actor(token)
    response = await api.set_schedule(
        hospital.id,
        api.ScheduleCreate(
            plan="PLAN_12", publish_days=list(range(7)), active_from=date(2026, 9, 16)
        ),
        adapter,
    )
    assert response["slots_created"] == 7
    assert db.get(ContentItem, original_id).first_published_at == first
    assert db.get(ContentItem, original_id).status == ContentStatus.REJECTED
    assert db.scalar(select(func.count()).select_from(ContentItem)) == 12
    response = await api.set_schedule(
        hospital.id,
        api.ScheduleCreate(plan="PLAN_12", publish_days=[0, 2, 4], active_from=date(2026, 9, 17)),
        adapter,
    )
    assert response["slots_created"] == 0
    latest = db.scalars(select(ContentSchedule).where(ContentSchedule.is_active)).one()
    assert (
        create_next_month_slots_for_schedule(
            db, latest, arrow.get("2026-09-01"), date(2026, 9, 1), date(2026, 9, 30)
        )
        is False
    )
    assert db.scalar(select(func.count()).select_from(ContentItem)) == 12
    assert old.plan == "PLAN_12"


async def test_generated_human_work_survives_reset(db, monkeypatch):
    hospital, _ = seed(db)
    item = db.scalars(select(ContentItem)).first()
    item.status, item.human_edited_at = ContentStatus.DRAFT, NOW
    original_id = item.id
    monkeypatch.setattr(api, "_schedule_readiness_blockers", AsyncMock(return_value=[]))
    monkeypatch.setattr(api.arrow, "now", lambda *args: arrow.get(NOW).to("Asia/Seoul"))
    await api.set_schedule(
        hospital.id,
        api.ScheduleCreate(
            plan="PLAN_12", publish_days=list(range(7)), active_from=date(2026, 9, 18)
        ),
        AsyncDB(db),
    )
    assert db.get(ContentItem, original_id).body == "preserved"
    assert db.get(ContentItem, original_id).human_edited_at is not None


async def test_next_month_contract_is_append_only(db, monkeypatch):
    hospital, old = seed(db)
    monkeypatch.setattr(handoffs.arrow, "now", lambda *args: arrow.get(NOW).to("Asia/Seoul"))
    await handoffs._sync_active_schedule_plan(AsyncDB(db), hospital.id, Plan.PLAN_20)
    db.flush()
    assert old.plan == "PLAN_12"
    future = db.scalars(select(ContentSchedule).where(ContentSchedule.is_active)).one()
    assert future.active_from == date(2026, 10, 1) and future.plan == "PLAN_20"
    await handoffs._sync_active_schedule_plan(AsyncDB(db), hospital.id, Plan.PLAN_20)
    db.flush()
    assert db.scalar(select(func.count()).select_from(ContentSchedule)) == 2
    plan = db.scalar(
        select(ContentSchedule.plan)
        .where(
            ContentSchedule.hospital_id == hospital.id,
            ContentSchedule.active_from < date(2026, 10, 1),
        )
        .order_by(ContentSchedule.active_from.desc(), ContentSchedule.created_at.desc())
        .limit(1)
    )
    assert plan == "PLAN_12"


def report():
    # 위치 인자로 만들면 감시 보고서에 사실이 하나 추가될 때마다 조용히 밀린다.
    return WatchdogReport(
        observed_at=NOW,
        kst_date="2026-09-15",
        redis_available=True,
        database_available=True,
        queue_canaries_current=True,
        stale_queues=(),
        stale_critical_queues=(),
        beat_alive=True,
        beat_lock_held=True,
        beat_last_schedule_run_at=NOW,
        beat_evidence="fresh",
        publish_checked=True,
        publish_due_remaining=0,
        publish_published_today=5,
        publish_missing=False,
        publish_gate_residual=False,
        publish_blocked_today=(),
        publish_partial=False,
        last_generation_batch_at=NOW,
        generation_batch_stale=False,
    )


def heartbeat(watchdog=None, facts=None, now=NOW):
    return build_fleet_heartbeat(
        watchdog or report(),
        facts or FleetFacts(2, 1, 0, 0, 2, 2),
        now=now,
        admin_base_url="http://localhost:3000",
    )


def test_heartbeat_healthy_no_due_and_retrying_not_human():
    text = heartbeat().message.fallback_text
    assert "관측 지표 양호" in text and "자동 복구 1건 · 사람의 개입 0건" in text
    assert (
        "관측 지표 양호"
        in heartbeat(replace(report(), publish_published_today=0)).message.fallback_text
    )
    assert not requires_operator_action("RETRYING", NOW + timedelta(hours=1), NOW)
    assert requires_operator_action("RETRYING", NOW - timedelta(seconds=1), NOW)
    assert not requires_operator_action("RUNNING", None, NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"publish_due_remaining": 1},
        {"generation_batch_stale": True},
        {"queue_canaries_current": False},
        {"beat_alive": False},
    ],
)
def test_heartbeat_degraded(change):
    assert "미완료 항목 있음" in heartbeat(replace(report(), **change)).message.fallback_text


@pytest.mark.parametrize(
    "change",
    [
        {"database_available": False},
        {"redis_available": False},
        {"publish_due_remaining": None},
        {"observed_at": NOW - timedelta(hours=1)},
    ],
)
def test_heartbeat_unknown(change):
    assert "관측 부족" in heartbeat(replace(report(), **change)).message.fallback_text


def test_missing_measurements_and_failed_run_are_not_normal():
    assert "관측 부족" in heartbeat(facts=FleetFacts(2, 0, 0, 0, 1, 0)).message.fallback_text
    assert "미완료 항목 있음" in heartbeat(facts=FleetFacts(2, 0, 0, 1, 2, 0)).message.fallback_text


def test_daily_outbox_real_dedupe_and_kst_boundary(db):
    intent = heartbeat()
    validate_message(intent.message, allowed_admin_base_url="http://localhost:3000")
    enqueue_notification_sync(db, intent, now=NOW)
    enqueue_notification_sync(db, heartbeat(facts=FleetFacts(2, 0, 2, 0, 2, 2)), now=NOW)
    db.commit()
    assert db.scalar(select(func.count()).select_from(NotificationOutbox)) == 1
    enqueue_notification_sync(db, heartbeat(now=NOW + timedelta(hours=7)), now=NOW)
    db.commit()
    assert db.scalar(select(func.count()).select_from(NotificationOutbox)) == 2


async def test_feedback_duplicate_safe_audited_retirement_without_generation(db):
    hospital, _ = seed(db)
    actor = SimpleNamespace(role="OWNER", email="doctor@example.com")
    payload = feedback_api.FeedbackInput(
        source="DIRECTOR", prefer_topics=["예방"], conversation_reference="9월 상담"
    )
    adapter = AsyncDB(db)
    first = await feedback_api.add_feedback(hospital.id, payload, adapter, actor)
    second = await feedback_api.add_feedback(hospital.id, payload, adapter, actor)
    assert first["id"] == second["id"]
    assert db.scalar(select(func.count()).select_from(DirectorDelta)) == 1
    audit = db.scalars(
        select(AdminAuditLog).where(AdminAuditLog.action == "director_feedback_created")
    ).first()
    assert audit.detail["conversation_reference"] == "9월 상담" and audit.actor == actor.email
    await feedback_api.retire_feedback(hospital.id, first["id"], adapter, actor)
    await feedback_api.retire_feedback(hospital.id, first["id"], adapter, actor)
    assert db.get(DirectorDelta, first["id"]).status == "RETIRED"
    assert db.scalar(select(func.count()).select_from(ContentItem)) == 5


async def test_feedback_tenant_and_empty_input_rejected(db):
    from fastapi import HTTPException

    hospital, _ = seed(db)
    actor = SimpleNamespace(role="OWNER", email="doctor@example.com")
    adapter = AsyncDB(db)
    first = await feedback_api.add_feedback(
        hospital.id,
        feedback_api.FeedbackInput(source="DIRECTOR", prefer_topics=["예방"]),
        adapter,
        actor,
    )
    other = Hospital(name="Other", slug=str(uuid.uuid4()))
    db.add(other)
    db.flush()
    with pytest.raises(HTTPException) as exc:
        await feedback_api.retire_feedback(other.id, first["id"], adapter, actor)
    assert exc.value.status_code == 404
    assert db.get(DirectorDelta, first["id"]).status == "ACTIVE"
    with pytest.raises(HTTPException) as exc:
        await feedback_api.add_feedback(
            hospital.id, feedback_api.FeedbackInput(source="DIRECTOR"), adapter, actor
        )
    assert exc.value.status_code == 422


async def test_reject_refreshes_late_publication_before_revision_and_withdrawal(db, monkeypatch):
    from sqlalchemy import update

    hospital, _ = seed(db)
    hospital.site_live = True
    item = db.scalars(select(ContentItem)).first()
    item.status, item.published_at, item.first_published_at, item.content_revision = (
        ContentStatus.DRAFT,
        None,
        None,
        1,
    )
    db.commit()
    events = []

    async def hospital_lock(*args):
        events.append("hospital")

    original_lock = api._lock_content_status

    async def late_publish(adapter, hid, cid, fallback):
        events.append("content")
        # A publisher won before the row lock: the loaded ORM snapshot is stale.
        db.execute(
            update(ContentItem)
            .where(ContentItem.id == cid)
            .values(
                status=ContentStatus.PUBLISHED,
                published_at=NOW,
                content_revision=9,
            )
            .execution_options(synchronize_session=False)
        )
        return await original_lock(adapter, hid, cid, fallback)

    monkeypatch.setattr(api, "acquire_hospital_advisory_lock", hospital_lock)
    monkeypatch.setattr(api, "_lock_content_status", late_publish)
    monkeypatch.setattr(api.indexnow, "enqueue_content_published", AsyncMock())
    revalidate = AsyncMock()
    monkeypatch.setattr(api, "trigger_content_site_revalidate_safe", revalidate)
    token = set_request_actor("doctor@example.com")
    try:
        await api.reject_content(
            hospital.id, item.id, api.RejectBody(reason="사실 정정 요청"), AsyncDB(db)
        )
    finally:
        reset_request_actor(token)
    assert events == ["hospital", "content"]
    assert item.content_revision == 10 and item.generation_claim_token is None
    assert item.first_published_at.replace(tzinfo=UTC) == NOW
    assert revalidate.await_args.kwargs["unpublished_from"].replace(tzinfo=UTC) == NOW
    audit = db.scalars(select(AdminAuditLog).where(AdminAuditLog.action == "reject_content")).one()
    assert audit.detail["previous_status"] == "PUBLISHED"


def test_registered_heartbeat_enqueues_only_and_dedupes(db, monkeypatch):
    from contextlib import contextmanager

    from app.core import database
    from app.core.celery_app import celery_app
    from app.services import fleet_heartbeat, pipeline_watchdog
    from app.workers.dispatch_envelope import TASK_PURPOSES
    from app.workers.notification_tasks import enqueue_fleet_heartbeat

    @contextmanager
    def sessions():
        yield db

    monkeypatch.setattr(database, "SyncSessionLocal", sessions)
    monkeypatch.setattr(pipeline_watchdog, "evaluate", lambda *args, **kwargs: report())
    monkeypatch.setattr(
        fleet_heartbeat, "collect_fleet_facts", lambda *args, **kwargs: FleetFacts(2, 1, 0, 0, 2, 1)
    )
    from app.core.config import settings

    monkeypatch.setattr(settings, "FLEET_HEARTBEAT_HOUR_KST", 0)
    first, second = enqueue_fleet_heartbeat.run(), enqueue_fleet_heartbeat.run()
    assert first["dedupe_key"] == second["dedupe_key"]
    assert second["status"] == "already_recorded"
    assert db.scalar(select(func.count()).select_from(NotificationOutbox)) == 1
    name = "app.workers.notification_tasks.enqueue_fleet_heartbeat"
    assert celery_app.conf.task_routes[name]["queue"] == "default"
    assert TASK_PURPOSES[name] == "fleet-heartbeat"
    assert celery_app.conf.beat_schedule["daily-fleet-heartbeat"]["task"] == name


async def test_future_head_preserves_current_repair_and_disabled_stays_disabled(db, monkeypatch):
    from app.services.schedule_reconciliation import effective_schedules_query

    hospital, old = seed(db)
    monkeypatch.setattr(handoffs.arrow, "now", lambda *args: arrow.get(NOW).to("Asia/Seoul"))
    await handoffs._sync_active_schedule_plan(AsyncDB(db), hospital.id, Plan.PLAN_20)
    db.flush()
    assert db.scalars(effective_schedules_query(date(2026, 9, 30))).one().id == old.id
    future = db.scalars(effective_schedules_query(date(2026, 10, 31))).one()
    assert future.plan == "PLAN_20"
    future.is_active = False
    db.flush()
    assert db.scalars(effective_schedules_query(date(2026, 10, 31))).all() == []


async def test_explicit_source_change_is_selective_and_fences_late_writeback(db):
    from app.services.essence_readiness import resolve_essence_readiness
    from app.services.knowledge_changes import (
        authority_refresh_required,
        invalidate_source_authority,
    )

    hospital, _ = seed(db)
    source_a, source_b = uuid.uuid4(), uuid.uuid4()
    base = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        is_base=True,
        source_asset_ids=[str(source_a), str(source_b)],
    )
    db.add(base)
    db.flush()
    items = list(db.scalars(select(ContentItem).order_by(ContentItem.sequence_no)))
    for item, source in zip(items, [source_a, source_b, source_b, source_b, source_b], strict=True):
        item.content_philosophy_id = base.id
        item.essence_status = "ALIGNED"
        item.essence_check_summary = {
            "generation_provenance": {
                "evidence_source_asset_ids": [str(source)],
            }
        }
    items[0].generation_claim_token = uuid.uuid4()
    db.commit()
    before_revision = items[0].content_revision
    first_publication = items[0].first_published_at
    changed = await invalidate_source_authority(
        AsyncDB(db), hospital.id, source_a, reason="SOURCE_EXCLUDED"
    )
    assert changed == [items[0].id]
    assert items[0].status == ContentStatus.REJECTED
    assert items[0].first_published_at == first_publication
    assert items[0].generation_claim_token is None
    assert items[0].content_revision == before_revision + 1
    assert items[1].status == ContentStatus.PUBLISHED and items[1].essence_status == "ALIGNED"
    assert authority_refresh_required(base)
    readiness = resolve_essence_readiness(base, [])
    assert readiness.current is None and readiness.public_philosophy is base
    changed_again = await invalidate_source_authority(
        AsyncDB(db), hospital.id, source_a, reason="SOURCE_EXCLUDED"
    )
    assert changed_again == [] and items[0].content_revision == before_revision + 1
    # Input additions not used by the existing base cause no invalidation.
    assert (
        await invalidate_source_authority(
            AsyncDB(db), hospital.id, uuid.uuid4(), reason="SOURCE_CORRECTED"
        )
        == []
    )


def test_public_surface_intent_rolls_back_and_survives_commit(db):
    from app.services.public_surface_intents import enqueue_public_surface_intent

    hospital, _ = seed(db)
    hospital.status = HospitalStatus.PAUSED
    enqueue_public_surface_intent(db, hospital)
    db.rollback()
    assert db.get(Hospital, hospital.id).status == HospitalStatus.ACTIVE
    assert db.scalar(select(func.count()).select_from(OperationRun)) == 0
    hospital.status = HospitalStatus.PAUSED
    run = enqueue_public_surface_intent(db, hospital)
    db.commit()
    db.expire_all()
    saved = db.get(OperationRun, run.id)
    assert saved.state == "RUNNING" and saved.request_payload["scope"] == "HOSPITAL"
    assert saved.result_summary["page_visibility_verified"] is False
    assert db.get(Hospital, hospital.id).status == HospitalStatus.PAUSED


def test_feedback_budget_dedupes_before_counting_without_truncating_existing(db):
    from app.services.director_delta import (
        ActiveDirectorDeltaLimit,
        DirectorDeltaInput,
        create_director_delta,
    )

    hospital, _ = seed(db)
    body = DirectorDeltaInput(source="DIRECTOR", prefer_topics=["예방", "예방"])
    assert body.prefer_topics == ["예방"]
    first = create_director_delta(db, hospital.id, body)
    assert create_director_delta(db, hospital.id, body).id == first.id
    too_large = DirectorDeltaInput(
        source="DIRECTOR", prefer_messages=[str(i) + "가" * 999 for i in range(7)]
    )
    with pytest.raises(ActiveDirectorDeltaLimit, match="6,000"):
        create_director_delta(db, hospital.id, too_large)
    assert db.scalar(select(func.count()).select_from(DirectorDelta)) == 1


def test_only_explicit_authority_change_reopens_automatic_base_review(monkeypatch):
    from app.models.essence import SourceStatus
    from app.services import essence_auto_review as review
    from app.services.knowledge_changes import AUTHORITY_CHANGE_FIELD

    base = SimpleNamespace(id=uuid.uuid4(), unsupported_gaps=[])
    source = SimpleNamespace(
        id=uuid.uuid4(), status=SourceStatus.PROCESSED, content_hash="hash", processed_at=NOW
    )
    monkeypatch.setattr(review, "_approved_unlocked", lambda *a: base)
    monkeypatch.setattr(review, "_required_sources", lambda *a: [source])
    monkeypatch.setattr(review, "_split_stale_error_sources", lambda rows, now: (rows, []))
    monkeypatch.setattr(review, "_drafts_for_snapshot", lambda *a: [])
    monkeypatch.setattr(review, "_claim_backoff_active", lambda *a, **kw: False)
    monkeypatch.setattr(review, "_notes_for_sources", lambda *a: ["evidence"])
    assert review.essence_refresh_needed(None, uuid.uuid4()) is False
    base.unsupported_gaps = [{"field": AUTHORITY_CHANGE_FIELD}]
    assert review.essence_refresh_needed(None, uuid.uuid4()) is True


async def test_future_contract_survives_current_month_schedule_edit(db, monkeypatch):
    hospital, old = seed(db)
    adapter = AsyncDB(db)
    monkeypatch.setattr(handoffs.arrow, "now", lambda *args: arrow.get(NOW).to("Asia/Seoul"))
    monkeypatch.setattr(api, "_schedule_readiness_blockers", AsyncMock(return_value=[]))
    await handoffs._sync_active_schedule_plan(adapter, hospital.id, Plan.PLAN_20)
    hospital.plan = Plan.PLAN_20
    db.commit()
    future = db.scalars(select(ContentSchedule).where(ContentSchedule.is_active)).one()
    result = await api.set_schedule(
        hospital.id,
        api.ScheduleCreate(
            plan="PLAN_20", publish_days=list(range(7)), active_from=date(2026, 9, 16)
        ),
        adapter,
    )
    assert result["future_contract_preserved"] is True
    assert result["plan"] == "PLAN_12"
    assert db.get(ContentSchedule, future.id).is_active
    assert db.get(ContentSchedule, future.id).plan == "PLAN_20"
    assert old.plan == "PLAN_12"


def test_withdrawn_article_cannot_be_published_by_a_new_base(db):
    from app.services.content_publication import assess_content_publication
    from app.services.content_visibility import assess_public_visibility

    hospital, _ = seed(db)
    item = db.scalars(select(ContentItem)).first()
    item.essence_check_summary = {"authority_change": {"source_ids": [str(uuid.uuid4())]}}
    assessment = assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))
    assert assessment.code == "CONTENT_AUTHORITY_CHANGED" and not assessment.publishable
    # Restore a valid-looking state to prove the independent public gate still blocks it.
    item.status = ContentStatus.PUBLISHED
    assert "CONTENT_AUTHORITY_CHANGED" in assess_public_visibility(item).blockers


def test_failed_publish_and_rescreen_cannot_erase_withdrawal(db):
    from app.services.content_publication import (
        apply_essence_revalidation,
        apply_publication_assessment,
        assess_content_publication,
    )

    hospital, _ = seed(db)
    item = db.scalars(select(ContentItem)).first()
    old_id, new_id = uuid.uuid4(), uuid.uuid4()
    item.content_philosophy_id = old_id
    item.essence_check_summary = {"authority_change": {"source_ids": [str(uuid.uuid4())]}}
    new_base = SimpleNamespace(id=new_id)
    for _ in range(3):
        assessment = assess_content_publication(item, new_base)
        apply_publication_assessment(item, assessment)
        assert not assessment.publishable
        assert item.content_philosophy_id == old_id
        assert item.essence_check_summary["authority_change"]
    assert apply_essence_revalidation(item, new_base) == "NEEDS_ESSENCE_REVIEW"
    assert item.content_philosophy_id == old_id


def make_execution(db):
    from app.workers.generation_run_control import ExplicitRunContext

    hospital, _ = seed(db)
    hospital.site_live = True
    item = db.scalars(select(ContentItem)).first()
    item.status = ContentStatus.DRAFT
    queued_token = uuid.uuid4()
    item.generation_claim_token = queued_token
    item.generation_claimed_at = NOW - timedelta(hours=3)
    worker = str(uuid.uuid4())
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="GENERATE_CONTENT_ITEM",
        state="RUNNING",
        task_id=worker,
        lease_owner=worker,
        lease_expires_at=NOW + timedelta(minutes=20),
        version=2,
        request_payload={"source_type": "content_item", "source_id": str(item.id)},
    )
    db.add(run)
    db.commit()
    return item, queued_token, ExplicitRunContext(run.id, worker, 2), run


def test_queue_wait_does_not_consume_execution_lease_and_duplicate_is_fenced(db):
    from app.workers.generation_execution_claim import begin_generation_execution

    item, queued, context, _ = make_execution(db)
    result = begin_generation_execution(db, item.id, queued, context, now=NOW)
    assert result is not None
    current, execution = result
    assert execution != queued
    assert current.generation_claim_token == execution
    assert current.generation_claimed_at.replace(tzinfo=UTC) == NOW
    assert begin_generation_execution(db, item.id, queued, context, now=NOW) is None


@pytest.mark.parametrize(
    "change", ["version", "worker", "expired", "tenant", "paused", "reclaimed"]
)
def test_execution_requires_current_run_and_item_ownership(db, change):
    from app.workers.generation_execution_claim import begin_generation_execution

    item, queued, context, run = make_execution(db)
    if change == "version":
        run.version += 1
    elif change == "worker":
        run.lease_owner = "other-worker"
    elif change == "expired":
        run.lease_expires_at = NOW
    elif change == "tenant":
        run.hospital_id = uuid.uuid4()
    elif change == "paused":
        item.hospital.status = HospitalStatus.PAUSED
    else:
        item.generation_claim_token = uuid.uuid4()
    db.commit()
    before = item.generation_claim_token
    assert begin_generation_execution(db, item.id, queued, context, now=NOW) is None
    assert db.get(ContentItem, item.id).generation_claim_token == before
