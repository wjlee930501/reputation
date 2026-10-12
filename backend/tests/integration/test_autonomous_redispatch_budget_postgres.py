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


def _nightly(
    db, *, updated_days_ago: int, operation_type: str = "NIGHTLY_CONTENT_GENERATION"
) -> OperationRun:
    run = OperationRun(
        operation_type=operation_type,
        state=OperationRunState.RUNNING,
        idempotency_key=f"nightly:{uuid.uuid4()}",
        task_id=f"nightly-{uuid.uuid4().hex[:8]}",
        request_payload={"window_start": "2026-09-08", "window_end": "2026-09-09"},
        attempt_count=1,
        started_at=NOW - timedelta(days=updated_days_ago),
        heartbeat_at=NOW - timedelta(days=updated_days_ago),
        lease_owner="dead-batch",
        lease_expires_at=NOW - timedelta(days=updated_days_ago) + timedelta(hours=2),
        requested_at=NOW - timedelta(days=updated_days_ago),
        updated_at=NOW - timedelta(days=updated_days_ago),
        version=3,
    )
    db.add(run)
    db.commit()
    return run


def test_a_ten_day_old_running_remnant_is_closed_with_one_incident(
    db, pg_conn, monkeypatch
) -> None:
    remnant = _nightly(db, updated_days_ago=11)
    recent = _nightly(db, updated_days_ago=9)

    _reconcile(monkeypatch, pg_conn)

    closed = _reload(db, remnant)
    assert closed.state == OperationRunState.FAILED
    assert closed.safe_error_code == "OPERATION_RUN_ABANDONED"
    assert closed.lease_owner is None and closed.lease_expires_at is None
    assert _reload(db, recent).state == OperationRunState.RUNNING
    incident = _cause_incident(db, "OPERATION_RUN_ABANDONED")
    assert incident is not None and incident.state == "OPEN"


def test_a_monthly_sov_period_run_gets_its_longer_window_in_sql(db, pg_conn, monkeypatch) -> None:
    waiting = _nightly(db, updated_days_ago=11, operation_type="MONTHLY_SOV_PERIOD")
    lost = _nightly(db, updated_days_ago=21, operation_type="MONTHLY_SOV_PERIOD")

    _reconcile(monkeypatch, pg_conn)

    assert _reload(db, waiting).state == OperationRunState.RUNNING
    assert _reload(db, lost).safe_error_code == "OPERATION_RUN_ABANDONED"


def test_the_cause_incident_closes_once_every_stopped_run_has_a_succeeded_retry(
    db, pg_conn, monkeypatch
) -> None:
    from app.workers.incident_backlog import close_resolved_backlog_incidents

    stopped = _run(
        db,
        operation_type="RUN_SOV",
        state=OperationRunState.QUEUED,
        queue="sov",
        minutes_ago=24 * 60 + 1,
        redispatch_count=3,
    )
    _reconcile(monkeypatch, pg_conn)
    incident = _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED")
    assert incident is not None and incident.state == "OPEN"
    stopped = _reload(db, stopped)

    # 운영 관제의 '작업 다시 시도'는 parent_run_id로 실패 실행을 가리키는 새 실행을 만든다.
    retry = OperationRun(
        hospital_id=stopped.hospital_id,
        operation_type="RUN_SOV",
        state=OperationRunState.RUNNING,
        parent_run_id=stopped.id,
        idempotency_key=f"retry:{uuid.uuid4()}",
        task_id=f"retry-{uuid.uuid4().hex[:8]}",
        request_payload=stopped.request_payload,
        requested_at=NOW + timedelta(minutes=5),
        version=1,
    )
    db.add(retry)
    db.commit()
    # 아직 성공하지 않은 재시도는 근거가 아니다.
    later = NOW + timedelta(minutes=30)
    close_resolved_backlog_incidents(db, now=later)
    db.expire_all()
    assert _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED").state == "OPEN"

    retry.state = OperationRunState.SUCCEEDED
    retry.completed_at = later
    db.commit()
    assert close_resolved_backlog_incidents(db, now=later) >= 1
    db.commit()
    db.expire_all()
    assert _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED").state in (
        "RECOVERED",
        "ACKNOWLEDGED",
    )


def _stop_incidents_and_outbox(db, code: str) -> tuple[int, int]:
    key = build_incident_key(
        "autonomous_recovery", "operation_recovery_stop", code, IncidentFingerprint.UNKNOWN
    )
    incidents = db.scalars(select(Incident).where(Incident.dedupe_key == key)).all()
    outbox = (
        db.scalars(
            select(NotificationOutbox).where(
                NotificationOutbox.incident_id.in_([incident.id for incident in incidents])
            )
        ).all()
        if incidents
        else []
    )
    return len(incidents), len(outbox)


def test_a_crash_between_failing_the_run_and_opening_the_incident_converges(
    db, pg_conn, monkeypatch
) -> None:
    run = _run(
        db,
        operation_type="RUN_SOV",
        state=OperationRunState.QUEUED,
        queue="sov",
        minutes_ago=24 * 60 + 1,
        redispatch_count=3,
    )
    real_open = autonomous_recovery._open_recovery_stop_incident
    calls = {"n": 0}

    def crash_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("DB connection lost after the run update")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(autonomous_recovery, "_open_recovery_stop_incident", crash_once)
    with pytest.raises(RuntimeError):
        _reconcile(monkeypatch, pg_conn)
    # 실행 상태와 사고는 함께 되돌아간다 — FAILED인데 사고가 없는 실행이 남지 않는다.
    assert _reload(db, run).state == OperationRunState.QUEUED

    _reconcile(monkeypatch, pg_conn)
    _reconcile(monkeypatch, pg_conn)

    assert _reload(db, run).safe_error_code == "OPERATION_REDISPATCH_EXHAUSTED"
    assert _stop_incidents_and_outbox(db, "OPERATION_REDISPATCH_EXHAUSTED") == (1, 1)


def test_a_failed_stop_left_without_its_incident_is_repaired_once(
    db, pg_conn, monkeypatch
) -> None:
    """이전 릴리스처럼 FAILED만 커밋되고 사고가 없는 실행은 다음 tick이 사고를 채운다."""

    orphan = _run(
        db,
        operation_type="RUN_SOV",
        state=OperationRunState.QUEUED,
        queue="sov",
        minutes_ago=90,
    )
    orphan.state = OperationRunState.FAILED
    orphan.safe_error_code = "OPERATION_RUN_ABANDONED"
    orphan.completed_at = NOW - timedelta(hours=2)
    db.commit()

    _reconcile(monkeypatch, pg_conn)
    _reconcile(monkeypatch, pg_conn)

    assert _stop_incidents_and_outbox(db, "OPERATION_RUN_ABANDONED") == (1, 1)
    incident = _cause_incident(db, "OPERATION_RUN_ABANDONED")
    assert incident is not None and incident.operation_run_id == orphan.id


def test_a_due_v0_is_not_starved_by_runs_still_inside_their_doubled_grace(
    db, pg_conn, monkeypatch
) -> None:
    waiting = [
        _run(
            db,
            operation_type="RUN_SOV",
            state=OperationRunState.QUEUED,
            queue="sov",
            # 기본 3시간은 지났지만 한 번 재배달돼 6시간을 기다리는 중이다.
            minutes_ago=4 * 60,
            redispatch_count=1,
        )
        for _ in range(autonomous_recovery._BATCH_SIZE)
    ]
    v0 = _run(
        db,
        operation_type="TRIGGER_V0_REPORT",
        state=OperationRunState.QUEUED,
        queue="reports",
        minutes_ago=46,
    )

    sent = _reconcile(monkeypatch, pg_conn)

    assert len(_sent_for(sent, v0)) == 1
    assert all(_sent_for(sent, run) == [] for run in waiting)


def _orphan(db, *, completed_ago: timedelta) -> OperationRun:
    orphan = _run(
        db,
        operation_type="RUN_SOV",
        state=OperationRunState.QUEUED,
        queue="sov",
        minutes_ago=180,
    )
    orphan.state = OperationRunState.FAILED
    orphan.safe_error_code = "OPERATION_REDISPATCH_EXHAUSTED"
    orphan.completed_at = NOW - completed_ago
    db.commit()
    return orphan


def _succeeded_retry(db, failed: OperationRun) -> None:
    db.add(
        OperationRun(
            hospital_id=failed.hospital_id,
            operation_type=failed.operation_type,
            state=OperationRunState.SUCCEEDED,
            parent_run_id=failed.id,
            idempotency_key=f"retry:{uuid.uuid4()}",
            task_id=f"retry-{uuid.uuid4().hex[:8]}",
            request_payload=failed.request_payload,
            requested_at=NOW + timedelta(minutes=5),
            completed_at=NOW + timedelta(minutes=30),
            version=1,
        )
    )
    db.commit()


def _close_resolved(db) -> None:
    from app.workers.incident_backlog import close_resolved_backlog_incidents

    close_resolved_backlog_incidents(db, now=NOW + timedelta(hours=1))
    db.commit()
    db.expire_all()


def test_a_repaired_stop_opens_its_episode_and_closes_after_its_retry(
    db, pg_conn, monkeypatch
) -> None:
    orphan = _orphan(db, completed_ago=timedelta(hours=2))

    _reconcile(monkeypatch, pg_conn)
    incident = _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED")
    assert incident is not None and incident.state == "OPEN"
    # 에피소드는 복구한 실행이 멈춘 때부터다 — 복구한 tick이 아니다.
    assert incident.first_seen_at == orphan.completed_at

    _succeeded_retry(db, orphan)
    _close_resolved(db)

    assert _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED").state in (
        "RECOVERED",
        "ACKNOWLEDGED",
    )


def test_an_unresolved_repaired_stop_keeps_the_incident_open(db, pg_conn, monkeypatch) -> None:
    orphan = _orphan(db, completed_ago=timedelta(hours=2))
    # 같은 tick에 같은 원인으로 새로 멈추는 실행.
    stopped = _run(
        db,
        operation_type="TRIGGER_V0_REPORT",
        state=OperationRunState.QUEUED,
        queue="reports",
        minutes_ago=6 * 60 + 1,
        redispatch_count=3,
    )

    _reconcile(monkeypatch, pg_conn)
    stopped = _reload(db, stopped)
    assert stopped.safe_error_code == "OPERATION_REDISPATCH_EXHAUSTED"
    assert _stop_incidents_and_outbox(db, "OPERATION_REDISPATCH_EXHAUSTED") == (1, 1)

    _succeeded_retry(db, stopped)
    _close_resolved(db)

    incident = _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED")
    assert incident.state == "OPEN"
    assert incident.first_seen_at <= orphan.completed_at
    assert incident.occurrence_count == 2


def _stop_directly(db, pg_conn, observed_at: datetime) -> OperationRun:
    run = _run(
        db,
        operation_type="RUN_SOV",
        state=OperationRunState.QUEUED,
        queue="sov",
        minutes_ago=30,
        redispatch_count=3,
    )
    session = _session(pg_conn)
    current = session.get(OperationRun, run.id)
    assert autonomous_recovery._stop_operation_run(
        session, current, observed_at, "OPERATION_REDISPATCH_EXHAUSTED"
    )
    session.close()
    db.expire_all()
    return run


def test_an_older_observation_never_moves_last_seen_backwards(db, pg_conn) -> None:
    later = NOW + timedelta(minutes=10)
    _stop_directly(db, pg_conn, later)  # 늦게 시작한 reconciler B가 먼저 기록한다
    _stop_directly(db, pg_conn, NOW)  # 먼저 시작한 A가 더 이른 시각으로 기록한다

    incident = _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED")
    assert incident.last_seen_at == later
    assert incident.occurrence_count == 2
    assert db.execute(
        autonomous_recovery._unrecorded_recovery_stops_query(later + timedelta(minutes=1))
    ).scalars().all() == []


def test_an_older_observation_does_not_reopen_an_acknowledged_incident(db, pg_conn) -> None:
    t1 = NOW + timedelta(minutes=10)
    _stop_directly(db, pg_conn, t1)
    incident = _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED")
    incident.state = "ACKNOWLEDGED"
    incident.acknowledged_at = t1
    db.commit()
    before = _stop_incidents_and_outbox(db, "OPERATION_REDISPATCH_EXHAUSTED")

    _stop_directly(db, pg_conn, NOW)

    incident = _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED")
    assert incident.state == "ACKNOWLEDGED"
    assert incident.episode_seq == 1
    assert _stop_incidents_and_outbox(db, "OPERATION_REDISPATCH_EXHAUSTED") == before

    # 닫힌 뒤의 새 관측은 새 에피소드로 다시 연다.
    _stop_directly(db, pg_conn, t1 + timedelta(minutes=5))
    incident = _cause_incident(db, "OPERATION_REDISPATCH_EXHAUSTED")
    assert incident.state == "OPEN" and incident.episode_seq == 2
    assert incident.first_seen_at == t1 + timedelta(minutes=5)
    assert _stop_incidents_and_outbox(db, "OPERATION_REDISPATCH_EXHAUSTED") == (1, before[1] + 1)
