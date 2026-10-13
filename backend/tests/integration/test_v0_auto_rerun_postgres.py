"""V0 자동 재실행은 같은 OperationRun을 미뤄 lineage를 지킨다(2026-10 감사 1-9a)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.workers import autonomous_recovery, tasks

NOW = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def _session(pg_conn) -> Session:
    return Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
def db(pg_conn):
    session = _session(pg_conn)
    try:
        yield session
    finally:
        session.close()


def _claimed_v0_run(db, *, reruns: int | None = None) -> OperationRun:
    hospital = Hospital(
        name="재실행 가상의원", slug=f"rerun-{uuid.uuid4().hex[:10]}", status=HospitalStatus.ACTIVE
    )
    db.add(hospital)
    db.flush()
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="TRIGGER_V0_REPORT",
        state=OperationRunState.RUNNING,
        task_id="v0-worker",
        lease_owner="v0-worker",
        lease_expires_at=NOW + timedelta(hours=1),
        version=3,
        attempt_count=1,
        request_payload=build_request_payload(
            DispatchPayload("hospital", str(hospital.id), "reports", (str(hospital.id),))
        ),
        result_summary={"kept": "yes"} if reruns is None else {"v0_auto_reruns": reruns},
        requested_at=NOW - timedelta(hours=1),
        queued_at=NOW - timedelta(hours=1),
    )
    db.add(run)
    db.commit()
    return run


def _worker(run, *, version: int = 3):
    return SimpleNamespace(
        request=SimpleNamespace(
            id=run.task_id,
            headers={"operation_run_id": str(run.id)},
            operation_run_claim_version=version,
        )
    )


def test_first_final_failure_defers_the_same_run_six_hours(db, pg_conn, monkeypatch):
    run = _claimed_v0_run(db)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _session(pg_conn))

    rerun_at = tasks._schedule_v0_auto_rerun(_worker(run), now=NOW)

    assert rerun_at == NOW + timedelta(hours=6)
    db.expire_all()
    stored = db.get(OperationRun, run.id)
    assert stored.state == OperationRunState.QUEUED
    assert stored.not_before_at == rerun_at
    assert stored.result_summary == {"kept": "yes", "v0_auto_reruns": 1}
    assert stored.lease_owner is None


def test_an_exhausted_budget_does_not_defer(db, pg_conn, monkeypatch):
    run = _claimed_v0_run(db, reruns=3)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _session(pg_conn))

    assert tasks._schedule_v0_auto_rerun(_worker(run), now=NOW) is None
    db.expire_all()
    assert db.get(OperationRun, run.id).state == OperationRunState.RUNNING


def test_a_stale_copy_cannot_schedule_a_rerun(db, pg_conn, monkeypatch):
    run = _claimed_v0_run(db)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _session(pg_conn))

    assert tasks._schedule_v0_auto_rerun(_worker(run, version=2), now=NOW) is None
    db.expire_all()
    stored = db.get(OperationRun, run.id)
    assert stored.state == OperationRunState.RUNNING
    assert stored.result_summary == {"kept": "yes"}


def test_exhaustion_terminalizes_with_the_domain_code(db, pg_conn, monkeypatch):
    """도메인 사고가 이미 열렸다 — 실행을 V0_REPORT_FAILED로 끝내 일반 실패 사고가 겹치지 않게 한다."""
    from app.services.operation_terminal_outcomes import terminal_outcome_identity

    run = _claimed_v0_run(db, reruns=3)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _session(pg_conn))

    tasks._finish_v0_run_failed(_worker(run), str(run.hospital_id))

    db.expire_all()
    stored = db.get(OperationRun, run.id)
    assert stored.state == OperationRunState.FAILED
    assert stored.safe_error_code == "V0_REPORT_FAILED"
    assert terminal_outcome_identity(stored) is None


def test_the_rerun_is_sent_once_after_its_time_with_the_same_lineage(db, pg_conn, monkeypatch):
    run = _claimed_v0_run(db)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _session(pg_conn))
    rerun_at = tasks._schedule_v0_auto_rerun(_worker(run), now=NOW)
    sent: list[dict] = []
    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: _session(pg_conn))
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: sent.append({"name": name, "args": args, **kwargs}),
    )

    def _mine():
        return [
            call
            for call in sent
            if isinstance(call.get("headers"), dict)
            and call["headers"].get("operation_run_id") == str(run.id)
        ]

    monkeypatch.setattr(autonomous_recovery, "_now", lambda: rerun_at - timedelta(minutes=1))
    autonomous_recovery.reconcile.run()
    assert _mine() == []

    monkeypatch.setattr(autonomous_recovery, "_now", lambda: rerun_at + timedelta(minutes=1))
    autonomous_recovery.reconcile.run()
    mine = _mine()
    assert len(mine) == 1
    assert mine[0]["name"] == "app.workers.tasks.trigger_v0_report"
    db.expire_all()
    assert db.get(OperationRun, run.id).result_summary["v0_auto_reruns"] == 1


async def test_rerun_incident_is_held_until_exhaustion_then_pages_once(pg_async_session, monkeypatch):
    """재실행 중에는 RETRYING·알림 보류, 소진되면 OPEN이 되고 같은 에피소드의 열림 알림 하나가 나간다."""
    from contextlib import asynccontextmanager

    from sqlalchemy import select

    from app.models.operations import Incident, NotificationOutbox
    from app.services import ops_incident_alerts
    from app.services.notification_store import claim_notification_batch

    db = pg_async_session
    hospital = Hospital(
        name="재실행 알림의원", slug=f"rerun-notice-{uuid.uuid4().hex[:10]}", status=HospitalStatus.ACTIVE
    )
    db.add(hospital)
    await db.flush()

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(ops_incident_alerts, "get_async_sessionmaker", lambda: _session)
    later = datetime.now(UTC) + timedelta(days=1)

    async def _fail(retry_until):
        return await ops_incident_alerts.open_ops_incident(
            pipeline="v0_report",
            object_type="hospital",
            object_id=str(hospital.id),
            incident_type="V0_REPORT_FAILED",
            safe_error_code="V0_REPORT_RETRIES_EXHAUSTED",
            problem="초기 진단 리포트 생성 재시도가 모두 실패했습니다.",
            customer_impact="초기 진단 리포트를 준비할 수 없습니다.",
            next_action="테스트",
            source_type="V0_REPORT",
            hospital_name="병원 초기 진단",
            hospital_id=hospital.id,
            actor="v0-report-worker",
            retry_until=retry_until,
        )

    async def _state(incident_id):
        return await db.scalar(
            select(Incident.state).where(Incident.id == incident_id).execution_options(
                populate_existing=True
            )
        )

    incident_id = await _fail(later + timedelta(hours=8))
    assert await _state(incident_id) == "RETRYING"
    assert await _fail(later + timedelta(hours=14)) == incident_id
    claimed = await claim_notification_batch(db, "test-worker", now=later)
    assert [row for row in claimed if row.incident_id == incident_id] == []

    await _fail(None)  # 예산 소진

    assert await _state(incident_id) == "OPEN"
    notices = list(
        (
            await db.execute(
                select(NotificationOutbox).where(NotificationOutbox.incident_id == incident_id)
            )
        ).scalars()
    )
    assert len(notices) == 1
    claimed = await claim_notification_batch(db, "test-worker", now=later)
    assert [row.id for row in claimed if row.incident_id == incident_id] == [notices[0].id]
