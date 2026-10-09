"""Redispatch ordering and fencing against a real PostgreSQL database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, update
from sqlalchemy.orm import Session, sessionmaker

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.workers import autonomous_recovery, operation_run_signals


def _session_factory(pg_engine) -> sessionmaker[Session]:
    return sessionmaker(bind=pg_engine, expire_on_commit=False)


def _seed_requested_run(factory: sessionmaker[Session]) -> tuple[uuid.UUID, uuid.UUID]:
    hospital_id = uuid.uuid4()
    run_id = uuid.uuid4()
    with factory() as db:
        db.add(
            Hospital(
                id=hospital_id,
                name="재배달 타이밍 의원",
                slug=f"redispatch-{uuid.uuid4().hex}",
                status=HospitalStatus.ACTIVE,
            )
        )
        db.add(
            OperationRun(
                id=run_id,
                hospital_id=hospital_id,
                operation_type="REBUILD_SITE",
                state=OperationRunState.REQUESTED,
                task_id="lost-before-publish",
                request_payload=build_request_payload(
                    DispatchPayload(
                        "hospital", str(hospital_id), "default", (str(hospital_id),)
                    )
                ),
                requested_at=datetime.now(UTC) - timedelta(minutes=10),
                safe_error_code="BROKER_TIMEOUT",
                safe_error_message="previous dispatch state unknown",
                version=1,
            )
        )
        db.commit()
    return hospital_id, run_id


def _cleanup(factory: sessionmaker[Session], hospital_id: uuid.UUID) -> None:
    with factory() as db:
        db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
        db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        db.commit()


def _finish_succeeded(factory: sessionmaker[Session], run_id: uuid.UUID) -> None:
    with factory() as worker_db:
        run = worker_db.get(OperationRun, run_id)
        assert run is not None
        assert run.state == OperationRunState.RUNNING
        run.state = OperationRunState.SUCCEEDED
        run.completed_at = datetime.now(UTC)
        run.lease_owner = None
        run.lease_expires_at = None
        run.version += 1
        worker_db.commit()


def test_fast_claim_after_publish_sees_committed_task_id_and_success_survives(
    pg_engine, monkeypatch, record_property
) -> None:
    factory = _session_factory(pg_engine)
    hospital_id, run_id = _seed_requested_run(factory)
    observed_at = datetime.now(UTC)
    published_task_ids: list[str] = []

    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", factory)

    def immediate_worker(_name: str, *, task_id: str, **_kwargs) -> None:
        published_task_ids.append(task_id)
        claimed_version = operation_run_signals._claim(
            run_id, task_id, observed_at, redelivered=False
        )
        assert claimed_version is not None
        _finish_succeeded(factory, run_id)

    monkeypatch.setattr(autonomous_recovery.celery_app, "send_task", immediate_worker)
    try:
        with factory() as redispatch_db:
            stale = redispatch_db.get(OperationRun, run_id)
            assert stale is not None
            assert autonomous_recovery._redispatch_operation_run(
                redispatch_db, stale, observed_at
            )

        with factory() as verify_db:
            current = verify_db.get(OperationRun, run_id)
            assert current is not None
            assert published_task_ids == [current.task_id]
            assert current.task_id != "lost-before-publish"
            assert current.state == OperationRunState.SUCCEEDED
            assert current.attempt_count == 1
            # redispatch intent, worker REQUESTED->QUEUED->RUNNING, terminal result.
            assert current.version == 5
            record_property("database", "PostgreSQL")
            record_property("published_task_id", current.task_id)
            record_property("state", current.state)
            record_property("attempt_count", current.attempt_count)
            record_property("version", current.version)
    finally:
        _cleanup(factory, hospital_id)


def test_send_failure_leaves_committed_requested_run_recoverable(
    pg_engine, monkeypatch, record_property
) -> None:
    factory = _session_factory(pg_engine)
    hospital_id, run_id = _seed_requested_run(factory)
    observed_at = datetime.now(UTC)

    attempted_task_ids: list[str] = []

    def fail_publish(*_args, task_id: str, **_kwargs) -> None:
        attempted_task_ids.append(task_id)
        raise OSError("fake broker rejected publish")

    monkeypatch.setattr(autonomous_recovery.celery_app, "send_task", fail_publish)
    try:
        with factory() as redispatch_db:
            run = redispatch_db.get(OperationRun, run_id)
            assert run is not None
            with pytest.raises(OSError, match="fake broker rejected publish"):
                autonomous_recovery._redispatch_operation_run(
                    redispatch_db, run, observed_at
                )
            redispatch_db.rollback()

        with factory() as verify_db:
            current = verify_db.get(OperationRun, run_id)
            assert current is not None
            assert current.state == OperationRunState.REQUESTED
            assert current.task_id != "lost-before-publish"
            assert current.version == 2
            first_failed_task_id = current.task_id

        with factory() as second_redispatch_db:
            retry = second_redispatch_db.get(OperationRun, run_id)
            assert retry is not None
            with pytest.raises(OSError, match="fake broker rejected publish"):
                autonomous_recovery._redispatch_operation_run(
                    second_redispatch_db, retry, observed_at
                )
            second_redispatch_db.rollback()

        with factory() as verify_db:
            current = verify_db.get(OperationRun, run_id)
            assert current is not None
            assert current.state == OperationRunState.REQUESTED
            assert current.task_id != first_failed_task_id
            assert attempted_task_ids == [first_failed_task_id, current.task_id]
            assert current.version == 3
            record_property("database", "PostgreSQL")
            record_property("state_after_two_failures", current.state)
            record_property("first_failed_task_id", first_failed_task_id)
            record_property("second_failed_task_id", current.task_id)
            record_property("version", current.version)
    finally:
        _cleanup(factory, hospital_id)


def test_duplicate_stale_redispatch_cannot_replace_durable_success(
    pg_engine, monkeypatch, record_property
) -> None:
    factory = _session_factory(pg_engine)
    hospital_id, run_id = _seed_requested_run(factory)
    observed_at = datetime.now(UTC)
    published_task_ids: list[str] = []

    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", factory)

    def immediate_worker(_name: str, *, task_id: str, **_kwargs) -> None:
        published_task_ids.append(task_id)
        claimed_version = operation_run_signals._claim(
            run_id, task_id, observed_at, redelivered=False
        )
        assert claimed_version is not None
        _finish_succeeded(factory, run_id)

    monkeypatch.setattr(autonomous_recovery.celery_app, "send_task", immediate_worker)
    try:
        with factory() as first_db, factory() as duplicate_db:
            first = first_db.get(OperationRun, run_id)
            duplicate = duplicate_db.get(OperationRun, run_id)
            assert first is not None and duplicate is not None
            assert autonomous_recovery._redispatch_operation_run(
                first_db, first, observed_at
            )
            assert not autonomous_recovery._redispatch_operation_run(
                duplicate_db, duplicate, observed_at
            )

        with factory() as verify_db:
            current = verify_db.get(OperationRun, run_id)
            assert current is not None
            assert current.state == OperationRunState.SUCCEEDED
            assert current.task_id == published_task_ids[0]
            assert len(published_task_ids) == 1
            assert current.attempt_count == 1
            assert current.version == 5
            record_property("database", "PostgreSQL")
            record_property("publish_count", len(published_task_ids))
            record_property("state", current.state)
            record_property("winning_task_id", current.task_id)
            record_property("version", current.version)
    finally:
        _cleanup(factory, hospital_id)


def test_cancelled_after_selection_is_not_resumed(pg_engine, monkeypatch, record_property) -> None:
    factory = _session_factory(pg_engine)
    hospital_id, run_id = _seed_requested_run(factory)
    observed_at = datetime.now(UTC)
    publishes: list[str] = []

    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda *_args, **kwargs: publishes.append(kwargs["task_id"]),
    )
    try:
        with factory() as stale_db, factory() as cancel_db:
            stale = stale_db.get(OperationRun, run_id)
            assert stale is not None
            cancelled = cancel_db.execute(
                update(OperationRun)
                .where(
                    OperationRun.id == run_id,
                    OperationRun.state == OperationRunState.REQUESTED,
                    OperationRun.version == 1,
                )
                .values(
                    state=OperationRunState.CANCELLED,
                    completed_at=observed_at,
                    version=2,
                )
                .returning(OperationRun.id)
            ).scalar_one()
            assert cancelled == run_id
            cancel_db.commit()

            assert not autonomous_recovery._redispatch_operation_run(
                stale_db, stale, observed_at
            )

        with factory() as verify_db:
            current = verify_db.get(OperationRun, run_id)
            assert current is not None
            assert current.state == OperationRunState.CANCELLED
            assert current.task_id == "lost-before-publish"
            assert current.version == 2
            assert publishes == []
            record_property("database", "PostgreSQL")
            record_property("state", current.state)
            record_property("publish_count", len(publishes))
            record_property("version", current.version)
    finally:
        _cleanup(factory, hospital_id)
