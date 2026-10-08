"""Real PostgreSQL proof for bounded legacy task-incident reconciliation."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.admin.operations_center_serializers import history
from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.services.legacy_task_incident_inventory import (
    LegacyIncidentInventory,
    inspect_legacy_task_incidents,
)
from app.services.legacy_task_incident_reconciliation import (
    reconcile_legacy_task_incidents,
)
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.services.operation_terminal_outcomes import terminal_outcome_identity
from app.utils import reconcile_legacy_task_incidents as reconcile_cli
from tests.db_env import require_db_url


def _factory() -> sessionmaker[Session]:
    return sessionmaker(
        create_engine(require_db_url("OPERATIONS_TEST_DATABASE_URL")),
        expire_on_commit=False,
    )


def _run(
    hospital_id: uuid.UUID,
    *,
    period: str,
    state: str,
    requested_at: datetime,
    explicit_target: bool = True,
) -> OperationRun:
    year, month = period.split("-")
    return OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="RUN_SOV",
        state=state,
        idempotency_key=f"monthly-sov:{hospital_id}:{period}:{uuid.uuid4()}",
        task_id=str(uuid.uuid4()),
        request_payload=(
            build_request_payload(
                DispatchPayload(
                    "hospital",
                    str(hospital_id),
                    "sov",
                    (str(hospital_id), "monthly", int(year), int(month)),
                )
            )
            if explicit_target
            else {}
        ),
        result_summary={"measurement_month": period},
        safe_error_code="TASK_FAILED" if state == "FAILED" else None,
        requested_at=requested_at,
    )


def _legacy(run: OperationRun, hospital_id: uuid.UUID, index: int) -> Incident:
    observed_at = run.requested_at
    return Incident(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_run_id=run.id,
        dedupe_key=f"legacy-worker-task:{run.id}",
        incident_type="BACKGROUND_TASK_FAILED",
        state="OPEN",
        severity="HIGH",
        customer_impact="legacy impact",
        source_type="OPERATION_RUN",
        source_id=str(run.id),
        safe_error_code="TASK_FAILED",
        safe_error_message="legacy failure",
        next_action="legacy action",
        admin_path="/operations",
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        created_at=observed_at + timedelta(microseconds=index),
        updated_at=observed_at,
    )


def _notice(incident: Incident, run: OperationRun, index: int) -> NotificationOutbox:
    now = datetime.now(UTC)
    return NotificationOutbox(
        id=uuid.uuid4(),
        hospital_id=incident.hospital_id,
        incident_id=incident.id,
        operation_run_id=run.id,
        dedupe_key=f"legacy-notice:{index}:{incident.id}",
        notification_type="INCIDENT_OPEN",
        channel="SLACK_DEV",
        state="SENT",
        payload={"legacy": index},
        fallback_text=f"legacy {index}",
        attempt_count=1,
        max_attempts=3,
        next_attempt_at=None,
        sent_at=now,
    )


def _constraint_incident(
    *, state: str, recovered_at: datetime | None, acknowledged_at: datetime | None
) -> Incident:
    now = datetime.now(UTC)
    return Incident(
        id=uuid.uuid4(),
        dedupe_key=f"constraint:{uuid.uuid4()}",
        incident_type="BACKGROUND_TASK_FAILED",
        state=state,
        severity="HIGH",
        customer_impact="constraint probe",
        source_type="OPERATION_RUN",
        source_id="constraint-probe",
        next_action="none",
        admin_path="/operations",
        first_seen_at=now,
        last_seen_at=now,
        recovered_at=recovered_at,
        acknowledged_at=acknowledged_at,
    )


def _outbox_checksum(db: Session, hospital_id: uuid.UUID) -> str:
    rows = [
        (
            str(row.id),
            str(row.incident_id),
            str(row.operation_run_id),
            row.dedupe_key,
            row.notification_type,
            row.state,
            row.payload,
        )
        for row in db.scalars(
            select(NotificationOutbox)
            .where(NotificationOutbox.hospital_id == hospital_id)
            .order_by(NotificationOutbox.id)
        )
    ]
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str).encode()
    ).hexdigest()


def _run_checksum(db: Session, hospital_id: uuid.UUID) -> str:
    rows = [
        (
            str(row.id),
            row.state,
            row.safe_error_code,
            row.request_payload,
            row.result_summary,
            row.version,
        )
        for row in db.scalars(
            select(OperationRun)
            .where(OperationRun.hospital_id == hospital_id)
            .order_by(OperationRun.id)
        )
    ]
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str).encode()
    ).hexdigest()


def test_bounded_conversion_is_idempotent_and_preserves_history(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    factory = _factory()
    hospital_id = uuid.uuid4()
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    runs = [
        _run(hospital_id, period="2026-08", state="FAILED", requested_at=now),
        _run(
            hospital_id,
            period="2026-08",
            state="FAILED",
            requested_at=now + timedelta(minutes=1),
        ),
        _run(
            hospital_id,
            period="2026-07",
            state="FAILED",
            requested_at=now + timedelta(minutes=2),
        ),
        _run(
            hospital_id,
            period="2026-07",
            state="SUCCEEDED",
            requested_at=now + timedelta(minutes=3),
        ),
        _run(
            hospital_id,
            period="2026-06",
            state="FAILED",
            requested_at=now + timedelta(minutes=4),
        ),
        _run(
            hospital_id,
            period="2026-09",
            state="SUCCEEDED",
            requested_at=now + timedelta(minutes=5),
        ),
        _run(
            hospital_id,
            period="2026-05",
            state="FAILED",
            requested_at=now + timedelta(minutes=6),
            explicit_target=False,
        ),
    ]
    legacy_runs = (runs[0], runs[1], runs[2], runs[4], runs[6])
    incidents = tuple(
        _legacy(run, hospital_id, index) for index, run in enumerate(legacy_runs)
    )
    with factory() as db:
        db.add(
            Hospital(
                id=hospital_id,
                name="Legacy incident reconciliation hospital",
                slug=f"legacy-incident-{hospital_id.hex}",
                status=HospitalStatus.ACTIVE,
            )
        )
        db.add_all(runs)
        db.flush()
        db.add_all(incidents)
        db.flush()
        db.add_all(
            _notice(incident, run, index)
            for index, (incident, run) in enumerate(zip(incidents, legacy_runs, strict=True))
        )
        db.commit()
        before_checksum = _outbox_checksum(db, hospital_id)
        before_run_checksum = _run_checksum(db, hospital_id)
        before_runs = db.scalar(
            select(func.count()).select_from(OperationRun).where(
                OperationRun.hospital_id == hospital_id
            )
        )
        assert inspect_legacy_task_incidents(db).convertible_open == 4
        assert inspect_legacy_task_incidents(db).unknown_open == 1

        monkeypatch.setattr(reconcile_cli, "SyncSessionLocal", factory)
        assert reconcile_cli.main() == 0
        first = json.loads(capsys.readouterr().out)
        assert first == {
            "status": "APPLIED",
            "converted": 2,
            "superseded": 1,
            "recovered": 1,
            "unknown": 1,
        }
        assert reconcile_cli.main() == 0
        second = json.loads(capsys.readouterr().out)
        assert second == {
            "status": "APPLIED",
            "converted": 0,
            "superseded": 0,
            "recovered": 0,
            "unknown": 1,
        }
        db.expire_all()
        assert inspect_legacy_task_incidents(db).convertible_open == 0
        assert inspect_legacy_task_incidents(db).unknown_open == 1
        assert _outbox_checksum(db, hospital_id) == before_checksum
        assert _run_checksum(db, hospital_id) == before_run_checksum
        assert db.scalar(
            select(func.count()).select_from(NotificationOutbox).where(
                NotificationOutbox.hospital_id == hospital_id
            )
        ) == len(incidents)
        assert db.scalar(
            select(func.count()).select_from(OperationRun).where(
                OperationRun.hospital_id == hospital_id
            )
        ) == before_runs

        stored = tuple(
            db.scalars(
                select(Incident)
                .where(Incident.hospital_id == hospital_id)
                .order_by(Incident.created_at, Incident.id)
            )
        )
        assert len(stored) == len(incidents)
        assert sum(row.incident_type == "OPERATION_TERMINAL_FAILED" for row in stored) == 2
        assert sum(row.state == "ACKNOWLEDGED" for row in stored) == 2
        june = next(row for row in stored if row.operation_run_id == runs[4].id)
        duplicate = next(row for row in stored if row.operation_run_id == runs[1].id)
        exact_success = next(row for row in stored if row.operation_run_id == runs[2].id)
        unknown = next(row for row in stored if row.operation_run_id == runs[6].id)
        assert duplicate.state == "ACKNOWLEDGED"
        assert duplicate.recovered_at is None
        duplicate_history = [event.event for event in history(duplicate)]
        assert "RECOVERED" not in duplicate_history
        assert duplicate_history[-1] == "ACKNOWLEDGED"
        assert exact_success.state == "ACKNOWLEDGED"
        assert exact_success.recovered_at is not None
        assert "RECOVERED" in [event.event for event in history(exact_success)]
        assert june.state == "OPEN"
        assert june.incident_type == "OPERATION_TERMINAL_FAILED"
        assert unknown.state == "OPEN"
        assert unknown.incident_type == "BACKGROUND_TASK_FAILED"
        audits = tuple(
            db.scalars(
                select(AdminAuditLog).where(
                    AdminAuditLog.target_id.in_(tuple(str(row.id) for row in incidents))
                )
            )
        )
        assert sorted(audit.action for audit in audits) == [
            "legacy_operation_incident_converted",
            "legacy_operation_incident_converted",
            "legacy_operation_incident_recovered",
            "legacy_operation_incident_superseded",
        ]
        assert all(audit.detail["legacy_operation_run_id"] for audit in audits)
        assert all(audit.detail["legacy_dedupe_key"] for audit in audits)

        db.execute(
            delete(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
        )
        db.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
        db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
        db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        db.commit()
    factory.kw["bind"].dispose()


def test_acknowledged_supersession_constraint_does_not_weaken_other_states() -> None:
    factory = _factory()
    now = datetime.now(UTC)
    for state, recovered_at in (("OPEN", now), ("RETRYING", now), ("RECOVERED", None)):
        with factory() as db, pytest.raises(IntegrityError):
            db.add(
                _constraint_incident(
                    state=state,
                    recovered_at=recovered_at,
                    acknowledged_at=None,
                )
            )
            db.commit()

    acknowledged = _constraint_incident(
        state="ACKNOWLEDGED",
        recovered_at=None,
        acknowledged_at=now,
    )
    with factory() as db:
        db.add(acknowledged)
        db.commit()
        assert db.get(Incident, acknowledged.id).recovered_at is None
        db.delete(acknowledged)
        db.commit()
    factory.kw["bind"].dispose()


def test_unknown_rows_do_not_starve_a_later_convertible_row() -> None:
    factory = _factory()
    hospital_id = uuid.uuid4()
    now = datetime(2026, 10, 9, 13, 0, tzinfo=UTC)
    unknown_runs = [
        _run(
            hospital_id,
            period="2026-05",
            state="FAILED",
            requested_at=now + timedelta(seconds=index),
            explicit_target=False,
        )
        for index in range(55)
    ]
    convertible = _run(
        hospital_id,
        period="2026-04",
        state="FAILED",
        requested_at=now + timedelta(minutes=2),
    )
    context_unknown = _run(
        hospital_id,
        period="2026-04",
        state="FAILED",
        requested_at=now + timedelta(seconds=90),
    )
    context_unknown.operation_type = "REGENERATE_CONTENT"
    context_unknown.request_payload = build_request_payload(
        DispatchPayload("content_item", str(uuid.uuid4()), "content", (str(uuid.uuid4()),))
    )
    tenant_mismatch = _run(
        hospital_id,
        period="2026-04",
        state="FAILED",
        requested_at=now + timedelta(seconds=91),
    )
    runs = (*unknown_runs, context_unknown, tenant_mismatch, convertible)
    incidents = tuple(_legacy(run, hospital_id, index) for index, run in enumerate(runs))
    incidents[-2].hospital_id = None
    with factory() as db:
        db.add(
            Hospital(
                id=hospital_id,
                name="Legacy starvation hospital",
                slug=f"legacy-starvation-{hospital_id.hex}",
            )
        )
        db.add_all(runs)
        db.flush()
        db.add_all(incidents)
        db.commit()

        result = reconcile_legacy_task_incidents(db, limit=1)

        assert result.converted == 1
        assert result.unknown == 57
        assert db.get(Incident, incidents[-1].id).incident_type == "OPERATION_TERMINAL_FAILED"
        assert inspect_legacy_task_incidents(db) == LegacyIncidentInventory(
            convertible_open=0,
            unknown_open=57,
        )
        db.execute(delete(Incident).where(Incident.id.in_(tuple(row.id for row in incidents))))
        db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
        db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        db.commit()
    factory.kw["bind"].dispose()


def test_closed_canonical_is_reopened_for_a_fresh_unresolved_failure() -> None:
    factory = _factory()
    hospital_id = uuid.uuid4()
    now = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
    failed = _run(
        hospital_id,
        period="2026-03",
        state="FAILED",
        requested_at=now,
    )
    legacy = _legacy(failed, hospital_id, 0)
    identity = terminal_outcome_identity(failed)
    assert identity is not None
    canonical = _legacy(failed, hospital_id, 1)
    canonical.id = uuid.uuid4()
    canonical.operation_run_id = None
    canonical.dedupe_key = identity.dedupe_key
    canonical.incident_type = "OPERATION_TERMINAL_FAILED"
    canonical.source_type = "OPERATION_OUTCOME"
    canonical.source_id = identity.source_id
    canonical.state = "ACKNOWLEDGED"
    canonical.recovered_at = now
    canonical.acknowledged_at = now
    with factory() as db:
        db.add(
            Hospital(
                id=hospital_id,
                name="Closed canonical hospital",
                slug=f"closed-canonical-{hospital_id.hex}",
            )
        )
        db.add(failed)
        db.flush()
        db.add_all((canonical, legacy))
        db.commit()

        result = reconcile_legacy_task_incidents(db)

        db.refresh(canonical)
        db.refresh(legacy)
        assert result.converted == 0
        assert result.superseded == 1
        assert canonical.state == "OPEN"
        assert canonical.recovered_at is None
        assert canonical.acknowledged_at is None
        assert canonical.episode_seq == 2
        assert legacy.state == "ACKNOWLEDGED"
        assert legacy.recovered_at is None
        assert db.scalar(
            select(func.count()).select_from(NotificationOutbox).where(
                NotificationOutbox.hospital_id == hospital_id
            )
        ) == 0
        db.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
        db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
        db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        db.commit()
    factory.kw["bind"].dispose()


def test_success_recovers_only_failures_that_precede_it() -> None:
    factory = _factory()
    hospital_id = uuid.uuid4()
    now = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)
    first_failure = _run(
        hospital_id,
        period="2026-02",
        state="FAILED",
        requested_at=now,
    )
    success = _run(
        hospital_id,
        period="2026-02",
        state="SUCCEEDED",
        requested_at=now + timedelta(minutes=1),
    )
    later_failure = _run(
        hospital_id,
        period="2026-02",
        state="FAILED",
        requested_at=now + timedelta(minutes=2),
    )
    first_incident = _legacy(first_failure, hospital_id, 0)
    later_incident = _legacy(later_failure, hospital_id, 1)
    with factory() as db:
        db.add(
            Hospital(
                id=hospital_id,
                name="Legacy chronology hospital",
                slug=f"legacy-chronology-{hospital_id.hex}",
            )
        )
        db.add_all((first_failure, success, later_failure))
        db.flush()
        db.add_all((first_incident, later_incident))
        db.commit()

        result = reconcile_legacy_task_incidents(db)

        db.refresh(first_incident)
        db.refresh(later_incident)
        assert result.recovered == 1
        assert result.converted == 1
        assert first_incident.state == "ACKNOWLEDGED"
        assert first_incident.recovered_at is not None
        assert later_incident.state == "OPEN"
        assert later_incident.incident_type == "OPERATION_TERMINAL_FAILED"
        assert later_incident.recovered_at is None
        db.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
        db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
        db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        db.commit()
    factory.kw["bind"].dispose()
