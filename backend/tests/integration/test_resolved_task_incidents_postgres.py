"""Real PostgreSQL proof for one period-scoped terminal operation exception."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.workers import task_incident_control
from tests.db_env import require_db_url


def _database_url() -> str:
    return require_db_url("OPERATIONS_TEST_DATABASE_URL")


@pytest.fixture
def operation_store(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(_database_url())
    factory = sessionmaker(engine, expire_on_commit=False, class_=Session)
    hospital_id = uuid.uuid4()
    with factory() as db:
        db.add(
            Hospital(
                id=hospital_id,
                name="Task15 기간 예외 테스트의원",
                slug=f"task15-terminal-{hospital_id.hex[:10]}",
                status=HospitalStatus.ACTIVE,
            )
        )
        db.commit()
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", factory)
    try:
        yield factory, hospital_id
    finally:
        with factory() as db:
            run_ids = tuple(
                db.scalars(select(OperationRun.id).where(OperationRun.hospital_id == hospital_id))
            )
            incident_ids = tuple(
                db.scalars(select(Incident.id).where(Incident.hospital_id == hospital_id))
            )
            if incident_ids:
                db.execute(
                    delete(NotificationOutbox).where(
                        NotificationOutbox.incident_id.in_(incident_ids)
                    )
                )
                db.execute(delete(Incident).where(Incident.id.in_(incident_ids)))
            if run_ids:
                # AdminAuditLog is deliberately append-only. The test removes its
                # mutable fixtures but preserves the supersession evidence exactly
                # as production retirement must preserve historical audit rows.
                db.execute(delete(OperationRun).where(OperationRun.id.in_(run_ids)))
            db.execute(delete(Hospital).where(Hospital.id == hospital_id))
            db.commit()
        engine.dispose()


def _monthly_run(
    factory: sessionmaker[Session],
    hospital_id: uuid.UUID,
    *,
    period: str,
    state: str,
) -> OperationRun:
    year_text, month_text = period.split("-", 1)
    task_id = str(uuid.uuid4())
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="RUN_SOV",
        state=state,
        idempotency_key=f"monthly-sov:{hospital_id}:{period}:attempt:{task_id}",
        task_id=task_id,
        request_payload=build_request_payload(
            DispatchPayload(
                "hospital",
                str(hospital_id),
                "sov",
                (str(hospital_id), "monthly", int(year_text), int(month_text)),
            )
        ),
        result_summary={"measurement_month": period},
        requested_at=datetime.now(UTC),
    )
    with factory() as db:
        db.add(run)
        db.commit()
    return run


def _task(run: OperationRun) -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(headers={"operation_run_id": str(run.id)})
    )


def test_same_period_retry_supersedes_one_card_and_preserves_attempt_history(
    operation_store,
) -> None:
    factory, hospital_id = operation_store
    first = _monthly_run(factory, hospital_id, period="2026-08", state="FAILED")
    second = _monthly_run(factory, hospital_id, period="2026-08", state="FAILED")

    assert task_incident_control.record_task_failure(_task(first), first.task_id) is True
    assert task_incident_control.record_task_failure(_task(second), second.task_id) is True

    with factory() as db:
        incidents = tuple(
            db.scalars(
                select(Incident).where(
                    Incident.hospital_id == hospital_id,
                    Incident.incident_type == "OPERATION_TERMINAL_FAILED",
                )
            )
        )
        assert len(incidents) == 1
        assert incidents[0].operation_run_id == second.id
        assert incidents[0].occurrence_count == 2
        assert incidents[0].state == "OPEN"
        assert db.scalar(
            select(func.count(OperationRun.id)).where(
                OperationRun.id.in_((first.id, second.id))
            )
        ) == 2
        superseded = db.scalar(
            select(AdminAuditLog).where(
                AdminAuditLog.action == "operation_attempt_superseded",
                AdminAuditLog.target_id == str(incidents[0].id),
            )
        )
        assert superseded is not None
        assert superseded.detail["superseded_run_id"] == str(first.id)
        assert superseded.detail["superseding_run_id"] == str(second.id)


def test_other_month_success_does_not_recover_prior_month(operation_store) -> None:
    factory, hospital_id = operation_store
    august = _monthly_run(factory, hospital_id, period="2026-08", state="FAILED")
    september = _monthly_run(factory, hospital_id, period="2026-09", state="SUCCEEDED")

    assert task_incident_control.record_task_failure(_task(august), august.task_id) is True
    assert task_incident_control.record_task_success(_task(september), september.task_id) is False

    with factory() as db:
        incident = db.scalar(
            select(Incident).where(
                Incident.hospital_id == hospital_id,
                Incident.incident_type == "OPERATION_TERMINAL_FAILED",
            )
        )
        assert incident is not None and incident.state == "OPEN"
        assert incident.source_id is not None
        assert "|2026-08|" in incident.source_id


def test_same_period_success_recovers_the_one_terminal_card(operation_store) -> None:
    factory, hospital_id = operation_store
    failed = _monthly_run(factory, hospital_id, period="2026-08", state="FAILED")
    succeeded = _monthly_run(factory, hospital_id, period="2026-08", state="SUCCEEDED")

    assert task_incident_control.record_task_failure(_task(failed), failed.task_id) is True
    assert task_incident_control.record_task_success(_task(succeeded), succeeded.task_id) is True

    with factory() as db:
        incident = db.scalar(
            select(Incident).where(
                Incident.hospital_id == hospital_id,
                Incident.incident_type == "OPERATION_TERMINAL_FAILED",
            )
        )
        assert incident is not None
        assert incident.state == "ACKNOWLEDGED"
        assert incident.recovered_at is not None
        assert db.scalar(
            select(func.count(Incident.id)).where(
                Incident.hospital_id == hospital_id,
                Incident.incident_type == "OPERATION_TERMINAL_FAILED",
            )
        ) == 1
