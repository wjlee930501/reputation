"""Full-Automatic W9(사고 위생)·W10(정체 감지기·일일 SLO) — 실제 PostgreSQL 사실로 확인한다.

1. 조용한 종류는 호출부가 HIGH를 줘도 LOW로 열리고, 옛 HIGH 행도 다음 관측에서 LOW가 된다.
2. 일회성 정리 CLI: 기본 dry-run은 아무것도 바꾸지 않고, --apply는 7일 넘은 조용한 사고·레거시 작업
   실패를 '해결' 알림 없이 닫고 새 조용한 사고는 LOW로만 내린다(감사 actor 남김).
3. 리드 진단 실패는 리드를 맡았거나 14일이 지나면 근거 정리가 조용히 닫는다(가상 행).
4. 정체 감지기: 48시간 안 미발행·6시간 무진전 슬롯의 다음 시도를 당기고 생성으로 보낸다 — 같은
   날 두 번 돌아도 행동은 한 번이다. 진전이 최근이면 건드리지 않는다.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, IncidentState, NotificationOutbox, OperationRun
from app.services import ops_incident_alerts
from app.utils import quiet_incident_settle
from app.workers import content_stall_detector, incident_backlog
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from tests.db_env import fail_unreachable, require_db_url

_URL_ENV = "TASK24_DATABASE_URL"
KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    url = require_db_url(_URL_ENV)
    engine = create_engine(url, future=True)
    try:
        engine.connect().close()
    except OperationalError as exc:
        engine.dispose()
        fail_unreachable(_URL_ENV, exc)
    session = Session(engine, expire_on_commit=False)
    async_engine = create_async_engine(
        url.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool
    )
    async_sessions = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(ops_incident_alerts, "get_async_sessionmaker", lambda: async_sessions)
    state = SimpleNamespace(db=session, hospital_ids=[], incident_ids=[], lead_ids=[])
    try:
        yield state
    finally:
        session.rollback()
        ids = state.hospital_ids
        session.execute(delete(NotificationOutbox).where(NotificationOutbox.hospital_id.in_(ids)))
        session.execute(delete(Incident).where(Incident.id.in_(state.incident_ids)))
        session.execute(delete(Incident).where(Incident.hospital_id.in_(ids)))
        session.execute(delete(OperationRun).where(OperationRun.hospital_id.in_(ids)))
        session.execute(delete(ContentItem).where(ContentItem.hospital_id.in_(ids)))
        session.execute(delete(ContentSchedule).where(ContentSchedule.hospital_id.in_(ids)))
        session.execute(delete(Hospital).where(Hospital.id.in_(ids)))
        session.commit()
        session.close()
        asyncio.run(async_engine.dispose())
        engine.dispose()


def _hospital(env, *, active: bool = False) -> Hospital:
    hospital = Hospital(id=uuid.uuid4(), name="W10 테스트의원", slug=f"w10-{uuid.uuid4().hex}")
    if active:
        hospital.status = HospitalStatus.ACTIVE
        hospital.site_live = True
    env.hospital_ids.append(hospital.id)
    env.db.add(hospital)
    env.db.flush()
    return hospital


def _incident(env, *, kind: str, severity: str = "HIGH", age: timedelta, source_id: str = "x"):
    now = datetime.now(UTC)
    incident = Incident(
        id=uuid.uuid4(),
        dedupe_key=f"w9:{uuid.uuid4().hex}",
        incident_type=kind,
        state=IncidentState.OPEN.value,
        severity=severity,
        customer_impact="테스트",
        source_type="TEST",
        source_id=source_id,
        next_action="테스트",
        admin_path="/operations",
        first_seen_at=now - age,
        last_seen_at=now - age,
        created_at=now - age,
        updated_at=now - age,
    )
    env.incident_ids.append(incident.id)
    env.db.add(incident)
    env.db.commit()
    return incident


def _open(hospital: Hospital, kind: str) -> uuid.UUID:
    return asyncio.run(
        ops_incident_alerts.open_ops_incident(
            pipeline="test",
            object_type="content",
            object_id=f"{hospital.id}:{kind}",
            incident_type=kind,
            safe_error_code=kind,
            problem="테스트",
            customer_impact="테스트",
            next_action="테스트",
            source_type="TEST",
            hospital_id=hospital.id,
            notify=False,
        )
    )


def test_quiet_incident_types_open_as_low(env) -> None:
    hospital = _hospital(env)
    env.db.commit()
    quiet_id = _open(hospital, "POST_PUBLISH_REVIEW_FLAGGED")
    loud_id = _open(hospital, "CONTENT_GENERATION_FAILED")
    env.db.expire_all()
    assert env.db.get(Incident, quiet_id).severity == "LOW"
    assert env.db.get(Incident, loud_id).severity == "HIGH"

    # 배포 전에 HIGH로 열린 행도 다음 관측에서 LOW로 내린다.
    row = env.db.get(Incident, quiet_id)
    row.severity = "HIGH"
    env.db.commit()
    assert _open(hospital, "POST_PUBLISH_REVIEW_FLAGGED") == quiet_id
    env.db.expire_all()
    assert env.db.get(Incident, quiet_id).severity == "LOW"


def test_cleanup_cli_is_dry_run_by_default_and_closes_silently_on_apply(env) -> None:
    old_quiet = _incident(env, kind="WEEKLY_SOV_MEASUREMENT_FAILED", age=timedelta(days=9))
    new_quiet = _incident(env, kind="WEEKLY_SOV_MEASUREMENT_FAILED", age=timedelta(days=1))
    old_task = _incident(
        env, kind="BACKGROUND_TASK_FAILED", age=timedelta(days=10), source_id=uuid.uuid4().hex
    )
    fresh_task = _incident(
        env, kind="BACKGROUND_TASK_FAILED", age=timedelta(days=2), source_id=uuid.uuid4().hex
    )
    actionable = _incident(env, kind="CONTENT_GENERATION_FAILED", age=timedelta(days=30))
    ids = [old_quiet.id, new_quiet.id, old_task.id, fresh_task.id, actionable.id]

    out = io.StringIO()
    planned = quiet_incident_settle.run(env.db, apply=False, out=out, incident_ids=ids)
    assert planned == {
        "CLOSE:WEEKLY_SOV_MEASUREMENT_FAILED": 1,
        "DEMOTE_LOW:WEEKLY_SOV_MEASUREMENT_FAILED": 1,
        "CLOSE:BACKGROUND_TASK_FAILED": 1,
    }
    assert out.getvalue().count("CANDIDATE ") == 3
    env.db.expire_all()
    assert {env.db.get(Incident, i).state for i in ids} == {"OPEN"}

    done = quiet_incident_settle.run(env.db, apply=True, out=io.StringIO(), incident_ids=ids)
    assert done == planned
    env.db.expire_all()
    assert env.db.get(Incident, old_quiet.id).state == "ACKNOWLEDGED"
    assert env.db.get(Incident, old_task.id).state == "ACKNOWLEDGED"
    assert env.db.get(Incident, new_quiet.id).state == "OPEN"
    assert env.db.get(Incident, new_quiet.id).severity == "LOW"
    assert env.db.get(Incident, fresh_task.id).state == "OPEN"
    assert env.db.get(Incident, actionable.id).state == "OPEN"
    actors = set(
        env.db.scalars(
            select(AdminAuditLog.actor).where(AdminAuditLog.target_id == str(old_task.id))
        )
    )
    assert actors == {quiet_incident_settle.ACTOR}
    # 조용히 닫는다 — '해결' 알림이 없다.
    assert (
        env.db.scalar(
            select(NotificationOutbox.id).where(NotificationOutbox.incident_id == old_task.id)
        )
        is None
    )
    # 두 번째 실행은 할 일이 없다(새 조용한 사고는 이미 LOW).
    assert quiet_incident_settle.run(env.db, apply=False, out=io.StringIO(), incident_ids=ids) == {}


def test_lead_diagnosis_failure_closes_when_lead_taken_or_aged(monkeypatch) -> None:
    now = datetime.now(UTC)
    resolver = incident_backlog.RESOLVERS["LEAD_DIAGNOSIS_FAILED"]
    leads = {"new": SimpleNamespace(status="NEW", purged_at=None)}
    leads["taken"] = SimpleNamespace(status="CONTACTED", purged_at=None)
    monkeypatch.setattr(incident_backlog, "_lead_measurement_recovered", lambda *_: None)
    monkeypatch.setattr(
        incident_backlog,
        "_lead_diagnosis_gone",
        lambda _db, incident: (SimpleNamespace(lead_id=incident.source_id), None),
    )
    db = SimpleNamespace(get=lambda _model, key: leads[key])

    def incident(lead: str, age_days: int):
        return SimpleNamespace(source_id=lead, first_seen_at=now - timedelta(days=age_days))

    assert resolver(db, incident("new", 2), now) is None
    assert resolver(db, incident("taken", 2), now) == "lead_followed_up"
    assert resolver(db, incident("new", 20), now) == "lead_diagnosis_incident_aged"
    assert "LEAD_DIAGNOSIS_FAILED" in incident_backlog.SILENT_RESOLUTION_TYPES


def _slot(env, hospital: Hospital, *, scheduled: date, attempt: dict, created_hours_ago: int):
    schedule = ContentSchedule(
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[0, 2, 4],
        active_from=scheduled - timedelta(days=30),
    )
    env.db.add(schedule)
    env.db.flush()
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.COLUMN,
        sequence_no=1,
        total_count=12,
        scheduled_date=scheduled,
        status=ContentStatus.DRAFT,
        essence_check_summary={GENERATION_ATTEMPT_KEY: attempt},
        created_at=datetime.now(UTC) - timedelta(hours=created_hours_ago),
    )
    env.db.add(item)
    env.db.commit()
    return item


def test_stall_detector_pulls_and_dispatches_once_per_day(env, monkeypatch) -> None:
    now = datetime.now(UTC)
    hospital = _hospital(env, active=True)
    tomorrow = now.astimezone(KST).date() + timedelta(days=1)
    stale_attempt = {
        "reason": "GENERATION_REJECTED",
        "retry_class": "SAMPLE_RECOVERABLE",
        "observed_at": (now - timedelta(hours=8)).isoformat(),
        "next_retry_at": (now + timedelta(hours=10)).isoformat(),
    }
    stalled = _slot(env, hospital, scheduled=tomorrow, attempt=stale_attempt, created_hours_ago=30)
    fresh = _slot(
        env,
        hospital,
        scheduled=tomorrow,
        attempt={**stale_attempt, "observed_at": (now - timedelta(hours=1)).isoformat()},
        created_hours_ago=30,
    )

    found = {item.id for item in content_stall_detector.find_stalled_items(env.db, now=now)}
    assert stalled.id in found and fresh.id not in found

    dispatched: list[uuid.UUID] = []
    monkeypatch.setattr(
        content_stall_detector,
        "_dispatch",
        lambda db, tasks, recorder_cls, ids, *rest: dispatched.extend(ids) or len(ids),
    )
    monkeypatch.setattr(
        content_stall_detector,
        "find_stalled_items",
        lambda db, *, now: [db.get(ContentItem, stalled.id)],
    )
    first = content_stall_detector.run_stall_detector(env.db, now=now)
    assert first.actions == {"PULL_RETRY": 1, "DISPATCH": 1}
    env.db.expire_all()
    attempt = env.db.get(ContentItem, stalled.id).essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert attempt["next_retry_at"] == now.isoformat()
    assert attempt["pulled_by"] == content_stall_detector.STALL_ACTOR

    # 같은 날 두 번째 점검: 당기기는 기록이 있어 다시 하지 않는다.
    second = content_stall_detector.run_stall_detector(env.db, now=now + timedelta(minutes=15))
    assert "PULL_RETRY" not in second.actions
    actions = env.db.scalar(
        select(OperationRun.id).where(
            OperationRun.operation_type == content_stall_detector.STALL_OPERATION_TYPE,
            OperationRun.hospital_id == hospital.id,
        )
    )
    assert actions is not None


def test_stall_dispatch_records_one_action_per_slot_per_day(env, monkeypatch) -> None:
    now = datetime.now(UTC)
    hospital = _hospital(env, active=True)
    item = _slot(
        env,
        hospital,
        scheduled=now.astimezone(KST).date(),
        attempt={},
        created_hours_ago=30,
    )
    assert content_stall_detector._claim_action(env.db, item, "DISPATCH", now=now) is True
    assert content_stall_detector._claim_action(env.db, item, "DISPATCH", now=now) is False
    assert (
        content_stall_detector._claim_action(env.db, item, "DISPATCH", now=now + timedelta(days=1))
        is True
    )


def test_daily_slo_reports_only_hospitals_that_missed(env) -> None:
    from app.services.autonomy_slo import collect_hospital_slo, slo_summary_lines

    hospital = _hospital(env, active=True)
    now_kst = datetime.now(KST).replace(hour=18, minute=0, second=0, microsecond=0)
    today = now_kst.date()
    _slot(env, hospital, scheduled=today, attempt={}, created_hours_ago=30)
    published = _slot(env, hospital, scheduled=today, attempt={}, created_hours_ago=30)
    published.status = ContentStatus.PUBLISHED
    published.first_published_at = now_kst.astimezone(UTC) - timedelta(hours=9)
    env.db.commit()

    slos = {
        slo.hospital_id: slo for slo in collect_hospital_slo(env.db, now=now_kst.astimezone(UTC))
    }
    mine = slos[hospital.id]
    assert (mine.due_today, mine.published_today, mine.missed_today) == (2, 1, 1)
    assert mine.missed
    lines = slo_summary_lines([mine])
    # 일정이 있으므로 이번 달 약속(PLAN_12)도 본다 — 남은 일정 1편으로는 10편이 모자란다.
    assert mine.month_target == 12 and mine.month_shortfall == 10
    assert lines == [
        f"• {hospital.name} — 오늘 예정 2편 중 미발행 1편 · 이번 달 약속 12편 중 남은 일정으로 10편 부족"
    ]

    # 08시 전에는 오늘 슬롯이 아직 첫 발행 기회를 맞지 않았다.
    morning = now_kst.replace(hour=7).astimezone(UTC)
    early = {slo.hospital_id: slo for slo in collect_hospital_slo(env.db, now=morning)}
    assert early[hospital.id].missed_today == 0


def test_stall_dispatch_claims_and_hands_off_to_item_task(env, monkeypatch) -> None:
    from app.workers import tasks as worker_tasks

    now = datetime.now(UTC)
    hospital = _hospital(env, active=True)
    item = _slot(
        env, hospital, scheduled=now.astimezone(KST).date(), attempt={}, created_hours_ago=30
    )
    sent: list[dict] = []
    monkeypatch.setattr(
        worker_tasks.celery_app, "send_task", lambda name, **kwargs: sent.append(kwargs)
    )
    batch_key = f"nightly:stall:{now:%Y%m%dT%H%M}"
    try:
        report = content_stall_detector.run_stall_detector(env.db, now=now)
        assert report.actions.get("DISPATCH") == 1
        assert len(sent) == 1 and sent[0]["args"][0] == str(item.id)
        env.db.expire_all()
        claimed = env.db.get(ContentItem, item.id)
        assert claimed.generation_claim_token is not None
        assert str(claimed.generation_claim_token) == sent[0]["args"][1]
        # 같은 날 다시 돌아도(claim이 풀렸다고 가정) 두 번째 디스패치는 없다.
        claimed.generation_claim_token = None
        claimed.generation_claimed_at = None
        env.db.commit()
        again = content_stall_detector.run_stall_detector(env.db, now=now + timedelta(minutes=15))
        assert "DISPATCH" not in again.actions and len(sent) == 1
    finally:
        env.db.rollback()
        env.db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital.id))
        env.db.execute(delete(OperationRun).where(OperationRun.idempotency_key == batch_key))
        env.db.commit()


def _writer_exhausted_attempt(now: datetime) -> dict:
    from app.workers.generation_attempt_state import (
        acquisition_completed,
        fresh_generation_attempt,
        reserve_acquisition,
    )

    attempt = fresh_generation_attempt(topic_id="topic-a")
    for index in range(6):
        acquisition_id = f"writer:ctx:topic-a:{index}"
        attempt = reserve_acquisition(attempt, acquisition_id=acquisition_id, kind="WRITER")
        attempt = acquisition_completed(
            attempt,
            acquisition_id=acquisition_id,
            transport_attempts=1,
            outcome="RESULT",
            payload={"index": index},
        )
    return {
        **attempt,
        "reason": "GENERATION_REJECTED",
        "retry_class": "SAMPLE_RECOVERABLE",
        "observed_at": (now - timedelta(hours=8)).isoformat(),
        "next_retry_at": (now + timedelta(hours=10)).isoformat(),
    }


def test_writer_exhausted_slot_goes_to_swap_never_pull_or_dispatch(env, monkeypatch) -> None:
    from app.workers.generation_attempt_state import current_topic_writers_exhausted

    now = datetime.now(UTC)
    hospital = _hospital(env, active=True)
    tomorrow = now.astimezone(KST).date() + timedelta(days=1)
    item = _slot(
        env,
        hospital,
        scheduled=tomorrow,
        attempt=_writer_exhausted_attempt(now),
        created_hours_ago=30,
    )
    assert current_topic_writers_exhausted(
        env.db.get(ContentItem, item.id).essence_check_summary[GENERATION_ATTEMPT_KEY]
    )
    monkeypatch.setattr(
        content_stall_detector,
        "find_stalled_items",
        lambda db, *, now: [db.get(ContentItem, item.id)],
    )
    swapped: list[uuid.UUID] = []
    monkeypatch.setattr(
        content_stall_detector,
        "_swap_writer_exhausted",
        lambda db, item_id, *, now: swapped.append(item_id) or False,
    )
    dispatched: list[uuid.UUID] = []
    monkeypatch.setattr(
        content_stall_detector,
        "_dispatch",
        lambda db, tasks, recorder_cls, ids, *rest: dispatched.extend(ids) or 0,
    )

    report = content_stall_detector.run_stall_detector(env.db, now=now)

    assert swapped == [item.id]
    assert dispatched == []
    assert "PULL_RETRY" not in report.actions
    env.db.expire_all()
    attempt = env.db.get(ContentItem, item.id).essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert attempt["next_retry_at"] == (now + timedelta(hours=10)).isoformat()


def test_writer_exhausted_slot_without_swap_budget_is_left_to_the_ladder(env, monkeypatch) -> None:
    now = datetime.now(UTC)
    hospital = _hospital(env, active=True)
    item = _slot(
        env,
        hospital,
        scheduled=now.astimezone(KST).date(),
        attempt=_writer_exhausted_attempt(now),
        created_hours_ago=30,
    )
    row = env.db.get(ContentItem, item.id)
    # 누락 방지 5(2026-10-14): Critical이 아닌 원인도 교체 두 번 — 두 번을 다 쓴 슬롯이다.
    row.topic_swap_history = [
        {"from_target_id": None, "swapped_at": (now - timedelta(days=3)).isoformat()},
        {"from_target_id": None, "swapped_at": (now - timedelta(days=2)).isoformat()},
    ]
    env.db.commit()
    monkeypatch.setattr(
        content_stall_detector,
        "find_stalled_items",
        lambda db, *, now: [db.get(ContentItem, item.id)],
    )
    swapped: list[uuid.UUID] = []
    monkeypatch.setattr(
        content_stall_detector,
        "_swap_writer_exhausted",
        lambda db, item_id, *, now: swapped.append(item_id) or False,
    )
    dispatched: list[uuid.UUID] = []
    monkeypatch.setattr(
        content_stall_detector,
        "_dispatch",
        lambda db, tasks, recorder_cls, ids, *rest: dispatched.extend(ids) or 0,
    )

    report = content_stall_detector.run_stall_detector(env.db, now=now)

    assert swapped == [] and dispatched == [] and report.actions == {}


def test_dispatch_never_sends_a_writer_exhausted_slot(env, monkeypatch) -> None:
    from app.workers import tasks as worker_tasks
    from app.workers.generation_batch_run import GenerationBatchRecorder

    now = datetime.now(UTC)
    hospital = _hospital(env, active=True)
    item = _slot(
        env,
        hospital,
        scheduled=now.astimezone(KST).date(),
        attempt={**_writer_exhausted_attempt(now), "next_retry_at": now.isoformat()},
        created_hours_ago=30,
    )
    sent: list[dict] = []
    monkeypatch.setattr(
        worker_tasks.celery_app, "send_task", lambda name, **kwargs: sent.append(kwargs)
    )
    window = content_stall_detector.stall_window(now)
    dispatched = content_stall_detector._dispatch(
        env.db, worker_tasks, GenerationBatchRecorder, [item.id], now, *window
    )
    assert dispatched == 0 and sent == []
    env.db.expire_all()
    assert env.db.get(ContentItem, item.id).generation_claim_token is None


def test_writer_exhausted_swap_without_a_candidate_topic_changes_nothing(env) -> None:
    now = datetime.now(UTC)
    hospital = _hospital(env, active=True)
    item = _slot(
        env,
        hospital,
        scheduled=now.astimezone(KST).date(),
        attempt=_writer_exhausted_attempt(now),
        created_hours_ago=30,
    )
    # 측정 질문이 없는 병원 — 교체 후보가 없으면 행을 바꾸지 않고 물러난다.
    assert content_stall_detector._swap_writer_exhausted(env.db, item.id, now=now) is False
    env.db.expire_all()
    assert not env.db.get(ContentItem, item.id).topic_swap_history


# ── 2026-10-14 누락 방지 1·3 ────────────────────────────────────────────────────


def _legacy_blocked_review(body_sha: str) -> dict:
    return {
        "status": "REVISE",
        "blocking": True,
        "schema_version": "content-review-v2",
        "candidate_sha256": body_sha,
        "findings": [
            {
                "kind": "HOSPITAL_FACT",
                "severity": "UNCERTAIN",
                "target": "CANDIDATE_TEXT",
                "quote": "근거 없는 병원 문장입니다.",
                "message": "근거가 없습니다.",
            }
        ],
    }


def test_stall_detector_sends_a_legacy_unrated_block_to_one_v3_rereview(env, monkeypatch) -> None:
    """누락 방지 3: 등급 없는 옛 차단 검수는 교체·당기기보다 먼저 생성(재검수)으로 보낸다."""

    from app.core.config import settings
    from app.workers import tasks

    monkeypatch.setattr(settings, "CONTENT_FINDING_TIERS_ENABLED", True)
    now = datetime.now(UTC)
    hospital = _hospital(env, active=True)
    yesterday = now.astimezone(KST).date() - timedelta(days=1)
    attempt = {
        "reason": "CONTENT_AI_HARD_FINDING",
        "retry_class": "SAMPLE_RECOVERABLE",
        "observed_at": (now - timedelta(hours=8)).isoformat(),
        "next_retry_at": (now + timedelta(hours=10)).isoformat(),
        "exhausted_days": 1,
    }
    item = _slot(env, hospital, scheduled=yesterday, attempt=attempt, created_hours_ago=30)
    item.title = "골다공증 안내"
    item.body = "## 본문\n근거 없는 병원 문장입니다."
    env.db.commit()
    sha = tasks.candidate_sha256(item)
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: attempt,
        "ai_review": _legacy_blocked_review(sha),
    }
    env.db.commit()

    assert tasks.legacy_tier_rereview_due(item) is True
    dispatched: list[uuid.UUID] = []
    monkeypatch.setattr(
        content_stall_detector,
        "_dispatch",
        lambda db, tasks_, recorder_cls, ids, *rest: dispatched.extend(ids) or len(ids),
    )
    monkeypatch.setattr(
        content_stall_detector,
        "find_stalled_items",
        lambda db, *, now: [db.get(ContentItem, item.id)],
    )
    report = content_stall_detector.run_stall_detector(env.db, now=now)
    assert dispatched == [item.id]
    assert "PULL_RETRY" not in report.actions and "TOPIC_SWAP" not in report.actions

    # 같은 본문으로 한 번 산 뒤에는 다시 사지 않는다(본문 hash당 한 번).
    item.essence_check_summary = {
        **item.essence_check_summary,
        tasks.LEGACY_TIER_REREVIEW_KEY: sha,
    }
    env.db.commit()
    assert tasks.legacy_tier_rereview_due(item) is False


def test_revision_not_created_leaves_an_audit_and_one_developer_incident(env, monkeypatch) -> None:
    """누락 방지 1: 판 미생성 건너뛰기는 발행 보류 감사와 글당 개발 사고 하나를 남기고, 발행되면 닫힌다."""

    from sqlalchemy.orm import sessionmaker

    from app.services.incident_types import IncidentAudience, incident_audience
    from app.services.post_publish_review_policy import AUTO_PUBLISH_BLOCKED_ACTION
    from app.workers import publish_revision_incident as revision

    url = require_db_url(_URL_ENV)
    sync_engine = env.db.get_bind()
    monkeypatch.setattr(revision, "SyncSessionLocal", sessionmaker(sync_engine))
    async_engine = create_async_engine(
        url.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool
    )
    sessions = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(revision, "get_async_sessionmaker", lambda: sessions)

    hospital = _hospital(env, active=True)
    today = datetime.now(KST).date()
    item = _slot(env, hospital, scheduled=today, attempt={}, created_hours_ago=30)
    fact = revision.RevisionNotCreated(
        content_id=item.id,
        hospital_id=hospital.id,
        hospital_name=hospital.name,
        scheduled_date=today,
        title="글",
        actor="SYSTEM_AUTO_PUBLISH",
        created_count=0,
        active_revision_set=False,
    )
    try:
        revision.record_revision_not_created(fact, run_async=asyncio.run)
        revision.record_revision_not_created(fact, run_async=asyncio.run)
    finally:
        asyncio.run(async_engine.dispose())

    audits = env.db.scalars(
        select(AdminAuditLog).where(
            AdminAuditLog.action == AUTO_PUBLISH_BLOCKED_ACTION,
            AdminAuditLog.target_id == str(item.id),
        )
    ).all()
    assert len(audits) == 2
    assert {audit.detail["code"] for audit in audits} == {"APPROVED_REVISION_NOT_CREATED"}
    incidents = env.db.scalars(select(Incident).where(Incident.hospital_id == hospital.id)).all()
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.incident_type == "AUTO_PUBLISH_REVISION_NOT_CREATED"
    assert incident.state == IncidentState.OPEN.value
    assert incident_audience(incident.incident_type) is IncidentAudience.DEVELOPER
    outbox = env.db.scalars(
        select(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital.id)
    ).all()
    assert len(outbox) == 1  # 처음 열 때만 알린다

    resolver = incident_backlog.RESOLVERS["AUTO_PUBLISH_REVISION_NOT_CREATED"]
    now = datetime.now(UTC)
    assert resolver(env.db, incident, now) is None
    item.status = ContentStatus.PUBLISHED
    env.db.commit()
    assert resolver(env.db, incident, now) == "content_published"
