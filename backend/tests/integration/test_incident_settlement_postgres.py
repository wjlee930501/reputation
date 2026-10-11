"""자동화가 끝낸 일을 사람이 확인 클릭하지 않게 한다(Phase 1-5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.workers.incident_backlog import settle_incidents

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


@pytest.fixture
def db(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _incident(db, *, state="RETRYING", source_type="LEAD_DIAGNOSIS", run=None,
              seen=NOW - timedelta(hours=3), recovered_at=None):
    incident = Incident(
        dedupe_key=f"settle-test:{uuid.uuid4()}",
        incident_type="RECOVER_LEAD_MEASUREMENT",
        state=state,
        severity="HIGH",
        customer_impact="테스트",
        source_type=source_type,
        next_action="테스트",
        admin_path="/operations",
        operation_run_id=run.id if run else None,
        sla_due_at=NOW + timedelta(hours=10) if state == "RETRYING" else None,
        first_seen_at=seen,
        last_seen_at=seen,
        recovered_at=recovered_at,
    )
    db.add(incident)
    db.flush()
    return incident


def _run(db, *, completed_at, state="SUCCEEDED"):
    run = OperationRun(
        operation_type="RECOVER_LEAD_MEASUREMENT", state=state, request_payload={},
        requested_at=completed_at - timedelta(minutes=5), completed_at=completed_at,
    )
    db.add(run)
    db.flush()
    return run


def _reload(db, incident) -> Incident:
    db.expire_all()
    return db.get(Incident, incident.id)


def test_retrying_with_a_later_linked_success_is_closed_by_the_machine(db):
    run = _run(db, completed_at=NOW - timedelta(hours=1))
    incident = _incident(db, run=run)

    settle_incidents(db, now=NOW)

    closed = _reload(db, incident)
    assert closed.state == "ACKNOWLEDGED"
    assert closed.acknowledged_by_id is None
    actions = db.scalars(
        select(AdminAuditLog.action).where(AdminAuditLog.target_id == str(incident.id))
    ).all()
    assert "incident_closed_by_linked_success" in actions


def test_a_success_older_than_the_last_failure_is_not_evidence(db):
    run = _run(db, completed_at=NOW - timedelta(hours=5))
    incident = _incident(db, run=run)

    settle_incidents(db, now=NOW)

    assert _reload(db, incident).state == "RETRYING"


def test_content_generation_incidents_are_left_to_their_own_recovery(db):
    run = _run(db, completed_at=NOW - timedelta(hours=1))
    incident = _incident(db, run=run, source_type="CONTENT_GENERATION")

    settle_incidents(db, now=NOW)

    assert _reload(db, incident).state == "RETRYING"


def test_a_delivered_open_is_followed_by_its_recovery_notice(db):
    run = _run(db, completed_at=NOW - timedelta(hours=1))
    incident = _incident(db, run=run)
    db.add(
        NotificationOutbox(
            dedupe_key=f"INCIDENT_OPEN:{incident.id}:e1", notification_type="INCIDENT_OPEN",
            channel="SLACK", state="SENT", incident_id=incident.id, payload={},
            fallback_text="테스트", next_attempt_at=None, sent_at=NOW - timedelta(hours=2),
        )
    )
    db.flush()

    settle_incidents(db, now=NOW)

    kinds = db.scalars(
        select(NotificationOutbox.notification_type).where(NotificationOutbox.incident_id == incident.id)
    ).all()
    assert sorted(kinds) == ["INCIDENT_OPEN", "INCIDENT_RECOVERED"]


def test_recovered_incidents_are_acknowledged_after_a_day(db):
    old = _incident(db, state="RECOVERED", recovered_at=NOW - timedelta(hours=25))
    fresh = _incident(db, state="RECOVERED", recovered_at=NOW - timedelta(hours=1))

    settle_incidents(db, now=NOW)

    assert _reload(db, old).state == "ACKNOWLEDGED"
    assert _reload(db, old).acknowledged_by_id is None
    assert _reload(db, fresh).state == "RECOVERED"
