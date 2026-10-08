"""긴 countdown 대신 DB에 남긴 시각으로 미룬 실행을 다시 보낸다(2026-10-08).

V0 비용 보류는 다음 비용 창(최대 약 24시간 뒤)까지 Celery countdown으로 기다렸다. 그 메시지는
브로커·봉투·lease 시계보다 오래 살아 배포 사이에 만료·중복됐다. 이제 워커가 실행을 QUEUED로
돌려놓고 `not_before_at`만 남기며, 자율 복구가 그 시각 뒤에 새 봉투로 한 번만 다시 보낸다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.workers import autonomous_recovery, dispatch_auth, operation_run_signals
from app.workers.generation_run_control import defer_operation_run

V0_TASK = "app.workers.tasks.trigger_v0_report"
NOW = datetime(2026, 10, 8, 14, 0, tzinfo=UTC)


def _session(pg_conn) -> Session:
    return Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
def db(pg_conn):
    session = _session(pg_conn)
    try:
        yield session
    finally:
        session.close()


def _claimed_v0_run(db) -> OperationRun:
    hospital = Hospital(
        name="비용 보류 가상의원",
        slug=f"deferral-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
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
        requested_at=NOW - timedelta(hours=1),
        queued_at=NOW - timedelta(hours=1),
    )
    db.add(run)
    db.commit()
    return run


def _worker(run, *, version: int):
    return SimpleNamespace(
        request=SimpleNamespace(
            id=run.task_id,
            headers={"operation_run_id": str(run.id)},
            operation_run_claim_version=version,
        )
    )


def _reconcile(monkeypatch, pg_conn, at: datetime) -> list[dict[str, object]]:
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: _session(pg_conn))
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: at)
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


def test_the_worker_hands_the_run_back_with_its_earliest_time(db, pg_conn, monkeypatch) -> None:
    run = _claimed_v0_run(db)
    not_before = NOW + timedelta(hours=10)

    assert defer_operation_run(db, _worker(run, version=3), not_before)

    db.refresh(run)
    assert run.state == OperationRunState.QUEUED
    assert run.not_before_at == not_before
    assert run.lease_owner is None and run.lease_expires_at is None
    assert run.version == 4

    # 태스크는 정상 반환하지만, 판이 바뀌어 뒤이은 성공 신호가 이 실행을 끝내지 못한다.
    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", lambda: _session(pg_conn))
    operation_run_signals.track_operation_postrun(
        task_id=run.task_id, task=_worker(run, version=3), state="SUCCESS"
    )
    db.expire_all()
    assert db.get(OperationRun, run.id).state == OperationRunState.QUEUED


def test_a_stale_copy_cannot_defer_the_run(db) -> None:
    run = _claimed_v0_run(db)

    assert not defer_operation_run(db, _worker(run, version=2), NOW + timedelta(hours=10))

    db.refresh(run)
    assert run.state == OperationRunState.RUNNING
    assert run.not_before_at is None


def test_a_deferred_run_is_resent_once_after_its_time_with_a_valid_envelope(
    db, pg_conn, monkeypatch
) -> None:
    run = _claimed_v0_run(db)
    not_before = NOW + timedelta(hours=10)
    assert defer_operation_run(db, _worker(run, version=3), not_before)
    # 미룬 지 3시간이 넘어도(QUEUED 유실 판정 유예) 정한 시각 전에는 보내지 않는다.
    assert _sent_for(_reconcile(monkeypatch, pg_conn, not_before - timedelta(minutes=1)), run) == []

    sent = _sent_for(_reconcile(monkeypatch, pg_conn, not_before + timedelta(minutes=1)), run)

    assert len(sent) == 1
    db.expire_all()
    current = db.get(OperationRun, run.id)
    assert sent[0]["task_id"] == current.task_id != "v0-worker"
    assert current.state == OperationRunState.QUEUED
    assert current.not_before_at is None
    # 같은 tick 뒤에는 다시 보내지 않는다(새 QUEUED는 유실 판정 유예를 따른다).
    assert _sent_for(_reconcile(monkeypatch, pg_conn, not_before + timedelta(minutes=2)), run) == []

    # 새로 서명된 봉투는 워커 검증을 통과한다.
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings, "WORKER_DISPATCH_SECRET", "worker-only-secret-32-bytes-minimum"
    )
    monkeypatch.setattr(dispatch_auth.settings, "REPUTATION_RELEASE_REVISION", "release-a")
    issued = int((not_before + timedelta(minutes=1)).timestamp())
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name=V0_TASK,
        task_id=str(sent[0]["task_id"]),
        args=list(sent[0]["args"]),
        kwargs={},
        retries=0,
        headers=dict(sent[0]["headers"]),
        now=issued,
    )
    dispatch_auth.validate_task_dispatch(
        task_name=V0_TASK,
        task_id=str(sent[0]["task_id"]),
        args=list(sent[0]["args"]),
        kwargs={},
        retries=0,
        headers=headers,
        now=issued + 5,
    )
