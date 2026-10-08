"""Expired REBUILD_SITE lease recovery against real PostgreSQL sessions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, NotificationOutbox, OperationRun, OperationRunState
from app.services.incident_safety import REBUILD_SITE_SWEEP_KEY_PREFIX
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.workers import autonomous_recovery, operation_run_signals


def _factory(pg_engine) -> sessionmaker[Session]:
    return sessionmaker(bind=pg_engine, expire_on_commit=False)


def _seed_running_site_build(
    factory: sessionmaker[Session],
    *,
    lease_expires_at: datetime,
    attempt_count: int,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    hospital_id = uuid.uuid4()
    run_id = uuid.uuid4()
    old_task_id = f"old-site-build-{uuid.uuid4()}"
    now = datetime.now(UTC)
    with factory() as db:
        db.add(
            Hospital(
                id=hospital_id,
                name="사이트 lease 복구 의원",
                slug=f"site-recovery-{uuid.uuid4().hex}",
                status=HospitalStatus.BUILDING,
                profile_complete=True,
                site_built=False,
            )
        )
        db.add(
            OperationRun(
                id=run_id,
                hospital_id=hospital_id,
                operation_type="REBUILD_SITE",
                state=OperationRunState.RUNNING,
                idempotency_key=(
                    f"{REBUILD_SITE_SWEEP_KEY_PREFIX}{hospital_id}:2026-10-09:0"
                ),
                task_id=old_task_id,
                attempt_count=attempt_count,
                heartbeat_at=now - timedelta(hours=1),
                lease_owner=old_task_id,
                lease_expires_at=lease_expires_at,
                started_at=now - timedelta(hours=1),
                requested_at=now - timedelta(hours=2),
                queued_at=now - timedelta(hours=2),
                request_payload=build_request_payload(
                    DispatchPayload(
                        "hospital", str(hospital_id), "default", (str(hospital_id),)
                    )
                ),
                version=7,
            )
        )
        db.commit()
    return hospital_id, run_id, old_task_id


def _cleanup(factory: sessionmaker[Session], hospital_id: uuid.UUID) -> None:
    with factory() as db:
        incident_ids = select(Incident.id).where(Incident.hospital_id == hospital_id)
        db.execute(
            delete(NotificationOutbox).where(NotificationOutbox.incident_id.in_(incident_ids))
        )
        db.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
        db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
        db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        db.commit()


def _patch_reconciler(monkeypatch, factory: sessionmaker[Session], now: datetime) -> None:
    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", factory)
    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", factory)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(autonomous_recovery, "require_dispatch", lambda *_args: None)
    monkeypatch.setattr(
        autonomous_recovery, "close_resolved_backlog_incidents", lambda _db: 0
    )
    monkeypatch.setattr(
        autonomous_recovery,
        "_dispatch_published_image_recertifications",
        lambda _db, _observed_at: 0,
    )


def test_expired_running_site_build_gets_one_replacement_and_one_final_result(
    pg_engine, monkeypatch, record_property
) -> None:
    factory = _factory(pg_engine)
    now = datetime.now(UTC)
    hospital_id, run_id, old_task_id = _seed_running_site_build(
        factory,
        lease_expires_at=now - timedelta(seconds=1),
        attempt_count=1,
    )
    published_task_ids: list[str] = []
    stale_claims: list[int | None] = []
    _patch_reconciler(monkeypatch, factory, now)

    def finish_site_build(
        task_name: str, *, task_id: str | None = None, **_kwargs
    ) -> None:
        if task_name != "app.workers.tasks.build_aeo_site":
            return
        assert task_id is not None
        published_task_ids.append(task_id)
        stale_claims.append(
            operation_run_signals._claim(
                run_id, old_task_id, now, redelivered=True
            )
        )
        claim_version = operation_run_signals._claim(
            run_id, task_id, now, redelivered=False
        )
        assert claim_version is not None
        with factory() as worker_db:
            hospital = worker_db.get(Hospital, hospital_id)
            assert hospital is not None
            hospital.site_built = True
            worker_db.commit()
        celery_task = SimpleNamespace(
            request=SimpleNamespace(
                headers={"operation_run_id": str(run_id)},
                operation_run_claim_version=claim_version,
            )
        )
        operation_run_signals._finish_from_signal(
            task_id,
            celery_task,
            OperationRunState.SUCCEEDED,
            None,
            None,
        )

    monkeypatch.setattr(autonomous_recovery.celery_app, "send_task", finish_site_build)
    try:
        result = autonomous_recovery.reconcile.run()

        with factory() as verify_db:
            run = verify_db.get(OperationRun, run_id)
            hospital = verify_db.get(Hospital, hospital_id)
            assert run is not None and hospital is not None
            assert result["operation_runs"] + result["site_builds"] == 1
            assert published_task_ids == [run.task_id]
            assert run.task_id != old_task_id
            assert stale_claims == [None]
            assert run.state == OperationRunState.SUCCEEDED
            assert run.attempt_count == 2
            assert hospital.site_built is True
            assert verify_db.scalar(
                select(func.count()).select_from(OperationRun).where(
                    OperationRun.hospital_id == hospital_id
                )
            ) == 1
            record_property("database", "PostgreSQL")
            record_property("replacement_count", len(published_task_ids))
            record_property("old_task_claim", "denied")
            record_property("state", run.state)
            record_property("site_built", hospital.site_built)
            record_property("result_rows", 1)
    finally:
        _cleanup(factory, hospital_id)


def test_live_lease_site_build_is_not_taken_over(
    pg_engine, monkeypatch, record_property
) -> None:
    factory = _factory(pg_engine)
    now = datetime.now(UTC)
    hospital_id, run_id, old_task_id = _seed_running_site_build(
        factory,
        lease_expires_at=now + timedelta(minutes=10),
        attempt_count=1,
    )
    publishes: list[str] = []
    _patch_reconciler(monkeypatch, factory, now)

    def capture_site_build(task_name: str, **kwargs) -> None:
        if task_name != "app.workers.tasks.build_aeo_site":
            return
        task_id = kwargs.get("task_id")
        assert isinstance(task_id, str)
        publishes.append(task_id)

    monkeypatch.setattr(
        autonomous_recovery.celery_app, "send_task", capture_site_build
    )
    try:
        result = autonomous_recovery.reconcile.run()

        with factory() as verify_db:
            run = verify_db.get(OperationRun, run_id)
            assert run is not None
            assert result["operation_runs"] + result["site_builds"] == 0
            assert publishes == []
            assert run.state == OperationRunState.RUNNING
            assert run.task_id == old_task_id
            assert run.attempt_count == 1
            assert run.version == 7
            record_property("database", "PostgreSQL")
            record_property("publish_count", len(publishes))
            record_property("state", run.state)
            record_property("version", run.version)
    finally:
        _cleanup(factory, hospital_id)


def test_exhausted_expired_site_build_stops_and_opens_at_most_one_exception(
    pg_engine, monkeypatch, record_property
) -> None:
    factory = _factory(pg_engine)
    now = datetime.now(UTC)
    hospital_id, run_id, old_task_id = _seed_running_site_build(
        factory,
        lease_expires_at=now - timedelta(seconds=1),
        attempt_count=3,
    )
    publishes: list[str] = []
    _patch_reconciler(monkeypatch, factory, now)

    def capture_site_build(task_name: str, **kwargs) -> None:
        if task_name != "app.workers.tasks.build_aeo_site":
            return
        task_id = kwargs.get("task_id")
        assert isinstance(task_id, str)
        publishes.append(task_id)

    monkeypatch.setattr(
        autonomous_recovery.celery_app, "send_task", capture_site_build
    )
    try:
        first = autonomous_recovery.reconcile.run()
        second = autonomous_recovery.reconcile.run()

        with factory() as verify_db:
            run = verify_db.get(OperationRun, run_id)
            incidents = list(
                verify_db.scalars(
                    select(Incident).where(Incident.hospital_id == hospital_id)
                )
            )
            assert run is not None
            assert first["operation_runs"] + first["site_builds"] == 0
            assert second["operation_runs"] + second["site_builds"] == 0
            assert publishes == []
            assert run.state == OperationRunState.FAILED
            assert run.task_id == old_task_id
            assert run.attempt_count == 3
            assert run.safe_error_code == "SITE_BUILD_RETRIES_EXHAUSTED"
            assert len(incidents) <= 1
            record_property("database", "PostgreSQL")
            record_property("publish_count", len(publishes))
            record_property("state", run.state)
            record_property("attempt_count", run.attempt_count)
            record_property("terminal_exception_count", len(incidents))
    finally:
        _cleanup(factory, hospital_id)
