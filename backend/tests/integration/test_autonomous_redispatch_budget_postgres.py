"""재배달 예산·lease 인수·버려진 RUNNING 정리를 실제 PostgreSQL에서 확인한다(2026-10-11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import (
    Incident,
    NotificationOutbox,
    OperationRun,
    OperationRunState,
)
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.workers import autonomous_recovery

NOW = datetime(2026, 10, 11, 5, 0, tzinfo=UTC)


def _session(pg_conn) -> Session:
    return Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
def db(pg_conn):
    session = _session(pg_conn)
    try:
        yield session
    finally:
        session.close()


def _hospital(db) -> Hospital:
    hospital = Hospital(
        name="재배달 예산 가상의원",
        slug=f"redispatch-budget-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
    )
    db.add(hospital)
    db.flush()
    return hospital


def _run(
    db,
    *,
    operation_type: str,
    state: OperationRunState,
    queue: str,
    args: tuple[object, ...] | None = None,
    minutes_ago: int = 0,
    redispatch_count: int = 0,
    lease_expires_at: datetime | None = None,
) -> OperationRun:
    hospital = _hospital(db)
    target = str(hospital.id)
    running = state == OperationRunState.RUNNING
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type=operation_type,
        state=state,
        task_id=f"copy-{uuid.uuid4().hex[:8]}",
        version=2,
        attempt_count=1 if running else 0,
        redispatch_count=redispatch_count,
        request_payload=build_request_payload(
            DispatchPayload("hospital", target, queue, (target, *(args or ())))
        ),
        requested_at=NOW - timedelta(minutes=minutes_ago),
        queued_at=NOW - timedelta(minutes=minutes_ago),
        started_at=NOW - timedelta(minutes=minutes_ago) if running else None,
        lease_owner="dead-worker" if lease_expires_at is not None else None,
        lease_expires_at=lease_expires_at,
    )
    db.add(run)
    db.commit()
    return run


def _reconcile(monkeypatch, pg_conn) -> list[dict[str, object]]:
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: _session(pg_conn))
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: NOW)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: sent.append({"name": name, "args": args, **kwargs}),
    )
    autonomous_recovery.reconcile.run()
    return sent


def _sent_for(sent, run) -> list[dict[str, object]]:
    return [
        call
        for call in sent
        if isinstance(call.get("headers"), dict)
        and call["headers"].get("operation_run_id") == str(run.id)
    ]


def _reload(db, run) -> OperationRun:
    db.expire_all()
    current = db.get(OperationRun, run.id)
    assert current is not None
    return current


def _cause_incident(db, code: str) -> Incident | None:
    key = build_incident_key(
        "autonomous_recovery", "operation_recovery_stop", code, IncidentFingerprint.UNKNOWN
    )
    return db.scalar(select(Incident).where(Incident.dedupe_key == key))


def test_a_lost_v0_copy_is_resent_after_45_minutes_and_counted(db, pg_conn, monkeypatch) -> None:
    run = _run(
        db,
        operation_type="TRIGGER_V0_REPORT",
        state=OperationRunState.QUEUED,
        queue="reports",
        minutes_ago=46,
    )
    original_task_id = run.task_id

    sent = _reconcile(monkeypatch, pg_conn)

    assert len(_sent_for(sent, run)) == 1
    current = _reload(db, run)
    assert current.state == OperationRunState.QUEUED
    assert current.task_id != original_task_id
    assert current.redispatch_count == 1


def test_a_publish_retry_does_not_spend_the_budget(db, pg_conn, monkeypatch) -> None:
    run = _run(
        db,
        operation_type="TRIGGER_V0_REPORT",
        state=OperationRunState.REQUESTED,
        queue="reports",
        minutes_ago=3,
        redispatch_count=3,
    )

    sent = _reconcile(monkeypatch, pg_conn)

    assert len(_sent_for(sent, run)) == 1
    assert _reload(db, run).redispatch_count == 3


def test_an_exhausted_budget_fails_runs_under_one_cause_incident(
    db, pg_conn, monkeypatch
) -> None:
    first = _run(
        db,
        operation_type="TRIGGER_V0_REPORT",
        state=OperationRunState.QUEUED,
        queue="reports",
        minutes_ago=6 * 60 + 1,
        redispatch_count=3,
    )
    second = _run(
        db,
        operation_type="RUN_SOV",
        state=OperationRunState.QUEUED,
        queue="sov",
        minutes_ago=24 * 60 + 1,
        redispatch_count=3,
    )

    sent = _reconcile(monkeypatch, pg_conn)

    assert _sent_for(sent, first) == [] and _sent_for(sent, second) == []
    for run in (first, second):
        current = _reload(db, run)
        assert current.state == OperationRunState.FAILED
        assert current.safe_error_code == "OPERATION_REDISPATCH_EXHAUSTED"
        assert current.completed_at == NOW
    incident = _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED")
    assert incident is not None
    assert incident.incident_type == "OPERATION_RECOVERY_EXHAUSTED"
    assert incident.state == "OPEN"
    assert incident.occurrence_count == 2
    outbox = db.scalars(
        select(NotificationOutbox).where(NotificationOutbox.incident_id == incident.id)
    ).all()
    assert len(outbox) == 1  # 같은 원인의 두 번째 실행은 Slack을 다시 보내지 않는다


def test_an_expired_sov_run_is_taken_over(db, pg_conn, monkeypatch) -> None:
    run = _run(
        db,
        operation_type="RUN_SOV",
        state=OperationRunState.RUNNING,
        queue="sov",
        minutes_ago=90,
        lease_expires_at=NOW - timedelta(seconds=1),
    )

    sent = _reconcile(monkeypatch, pg_conn)

    assert len(_sent_for(sent, run)) == 1
    current = _reload(db, run)
    assert current.state == OperationRunState.QUEUED
    assert current.lease_owner is None
    assert current.redispatch_count == 1


def test_an_expired_monthly_rebuild_is_stopped_not_rerun(db, pg_conn, monkeypatch) -> None:
    run = _run(
        db,
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.RUNNING,
        queue="reports",
        args=(2026, 9, True),
        minutes_ago=90,
        lease_expires_at=NOW - timedelta(seconds=1),
    )

    sent = _reconcile(monkeypatch, pg_conn)

    assert _sent_for(sent, run) == []
    current = _reload(db, run)
    assert current.state == OperationRunState.FAILED
    assert current.safe_error_code == "OPERATION_LEASE_EXPIRED"
    assert _cause_incident(db, "OPERATION_LEASE_EXPIRED") is not None


def test_an_expired_recertify_run_is_left_to_its_sweep(db, pg_conn, monkeypatch) -> None:
    run = _run(
        db,
        operation_type="RECERTIFY_PUBLISHED_IMAGE",
        state=OperationRunState.RUNNING,
        queue="content",
        minutes_ago=90,
        lease_expires_at=NOW - timedelta(seconds=1),
    )

    sent = _reconcile(monkeypatch, pg_conn)

    assert _sent_for(sent, run) == []
    assert _reload(db, run).state == OperationRunState.RUNNING
