import os
from enum import StrEnum

import pytest
import sqlalchemy as sa

import app.models as models
from app.core.database import Base


def _enum_values(enum_type: type[StrEnum]) -> set[str]:
    return {member.value for member in enum_type}


def test_operations_models_expose_exact_lifecycle_states() -> None:
    # Given: the public model package
    required = (
        "Incident",
        "IncidentState",
        "OperationRun",
        "OperationRunState",
        "NotificationOutbox",
        "NotificationOutboxState",
    )

    # When: the durable operations model contract is inspected
    missing = [name for name in required if not hasattr(models, name)]

    # Then: all models and exact state machines are available
    assert missing == []
    assert _enum_values(models.IncidentState) == {
        "OPEN",
        "RETRYING",
        "RECOVERED",
        "ACKNOWLEDGED",
    }
    assert _enum_values(models.OperationRunState) == {
        "REQUESTED",
        "QUEUED",
        "RUNNING",
        "SUCCEEDED",
        "PARTIAL",
        "FAILED",
        "CANCELLED",
    }
    assert _enum_values(models.NotificationOutboxState) == {
        "PENDING",
        "SENDING",
        "RETRYING",
        "HOLD",
        "SENT",
        "FAILED",
    }


def test_operations_tables_encode_keys_versions_leases_and_queue_indexes() -> None:
    # Given: SQLAlchemy metadata after importing the public model package
    assert models.OperationRun is not None

    # When: the three operations-control tables are inspected
    runs = Base.metadata.tables["operation_runs"]
    incidents = Base.metadata.tables["incidents"]
    outbox = Base.metadata.tables["notification_outbox"]

    # Then: optimistic concurrency, typed payloads, leases and stable keys are structural
    assert runs.c.version.nullable is False
    assert incidents.c.version.nullable is False
    assert outbox.c.version.nullable is False
    assert runs.c.hospital_id.nullable is True
    assert incidents.c.hospital_id.nullable is True
    assert outbox.c.hospital_id.nullable is True
    assert isinstance(runs.c.request_payload.type, sa.JSON)
    assert isinstance(runs.c.result_summary.type, sa.JSON)
    assert isinstance(outbox.c.payload.type, sa.JSON)
    assert isinstance(outbox.c.provider_response.type, sa.JSON)
    assert outbox.c.next_attempt_at.nullable is True
    assert "parent_run_id" in runs.c
    assert "retry_of_run_id" not in runs.c
    assert incidents.c.occurrence_count.nullable is False
    assert any(
        constraint.name == "ck_incidents_occurrence_count"
        for constraint in incidents.constraints
    )
    assert {"lease_owner", "lease_expires_at"} <= set(runs.c.keys())
    assert {"lease_owner", "lease_expires_at"} <= set(outbox.c.keys())
    assert any(index.name == "uq_operation_runs_active_idempotency" for index in runs.indexes)
    assert any(index.name == "uq_operation_runs_idempotency_scope" for index in runs.indexes)
    assert any(index.name == "ix_operation_runs_claim" for index in runs.indexes)
    assert any(index.name == "ix_notification_outbox_claim" for index in outbox.indexes)
    assert any(
        constraint.name == "uq_incidents_dedupe_key"
        for constraint in incidents.constraints
    )
    assert any(
        constraint.name == "uq_notification_outbox_dedupe_key"
        for constraint in outbox.constraints
    )
    scheduling_check = next(
        constraint
        for constraint in outbox.constraints
        if constraint.name == "ck_notification_outbox_retry_schedule"
    )
    scheduling_sql = str(scheduling_check.sqltext)
    assert "PENDING" in scheduling_sql and "RETRYING" in scheduling_sql
    assert "next_attempt_at IS NOT NULL" in scheduling_sql
    assert "SENDING" in scheduling_sql and "HOLD" in scheduling_sql
    assert "next_attempt_at IS NULL" in scheduling_sql


def test_postgres_preserves_recovery_and_scopes_terminal_idempotency() -> None:
    # Given: a real PostgreSQL database migrated through 0042
    database_url = os.getenv("OPERATIONS_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("OPERATIONS_TEST_DATABASE_URL is required for PostgreSQL constraint proof")
    engine = sa.create_engine(database_url)
    actor_a = "73000000-0000-0000-0000-000000000001"
    actor_b = "73000000-0000-0000-0000-000000000002"
    hospital_a = "73000000-0000-0000-0000-000000000003"
    hospital_b = "73000000-0000-0000-0000-000000000004"
    run_ids = [
        "73000000-0000-0000-0000-000000000005",
        "73000000-0000-0000-0000-000000000006",
        "73000000-0000-0000-0000-000000000007",
    ]
    incident_id = "73000000-0000-0000-0000-000000000008"
    cleanup = sa.text(
        "DELETE FROM incidents WHERE id=:incident_id; "
        "DELETE FROM operation_runs WHERE id = ANY(CAST(:run_ids AS uuid[])); "
        "DELETE FROM hospitals WHERE id IN (:hospital_a, :hospital_b); "
        "DELETE FROM admin_users WHERE id IN (:actor_a, :actor_b)"
    )
    parameters = {
        "actor_a": actor_a,
        "actor_b": actor_b,
        "hospital_a": hospital_a,
        "hospital_b": hospital_b,
        "run_ids": run_ids,
        "incident_id": incident_id,
    }
    try:
        with engine.begin() as connection:
            connection.execute(cleanup, parameters)
            connection.execute(
                sa.text(
                    "INSERT INTO admin_users (id,email,name,role,password_hash,is_active) VALUES "
                    "(:actor_a,'task7-a@example.test','Task 7 A','OWNER','x',true),"
                    "(:actor_b,'task7-b@example.test','Task 7 B','OWNER','x',true); "
                    "INSERT INTO hospitals (id,name,slug) VALUES "
                    "(:hospital_a,'Task 7 A','ops-task7-a'),"
                    "(:hospital_b,'Task 7 B','ops-task7-b')"
                ),
                parameters,
            )
            for run_id, actor_id, hospital_id in (
                (run_ids[0], actor_a, hospital_a),
                (run_ids[1], actor_b, hospital_a),
                (run_ids[2], actor_a, hospital_b),
            ):
                connection.execute(
                    sa.text(
                        "INSERT INTO operation_runs "
                        "(id,hospital_id,operation_type,state,idempotency_key,requested_by_id,"
                        "request_payload,completed_at) VALUES "
                        "(:id,:hospital_id,'REBUILD_REPORT','SUCCEEDED','same-client-key',"
                        ":actor_id,'{}',now())"
                    ),
                    {"id": run_id, "actor_id": actor_id, "hospital_id": hospital_id},
                )
            connection.execute(
                sa.text(
                    "INSERT INTO incidents "
                    "(id,dedupe_key,incident_type,state,severity,customer_impact,source_type,"
                    "next_action,admin_path,recovered_at) VALUES "
                    "(:id,'OPS-QA-TASK7-RECOVERY','QA','RECOVERED','LOW','none','QA',"
                    "'none','/operations',now())"
                ),
                {"id": incident_id},
            )
            recovered_at = connection.execute(
                sa.text("SELECT recovered_at FROM incidents WHERE id=:id"), {"id": incident_id}
            ).scalar_one()
            connection.execute(
                sa.text(
                    "UPDATE incidents SET state='ACKNOWLEDGED', acknowledged_at=now(), "
                    "acknowledged_by_id=:actor WHERE id=:id"
                ),
                {"actor": actor_a, "id": incident_id},
            )
            assert connection.execute(
                sa.text("SELECT recovered_at FROM incidents WHERE id=:id"), {"id": incident_id}
            ).scalar_one() == recovered_at

        # When: the exact terminal actor/hospital/operation/client scope is replayed
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO operation_runs "
                        "(id,hospital_id,operation_type,state,idempotency_key,requested_by_id,"
                        "request_payload,completed_at) VALUES "
                        "(gen_random_uuid(),:hospital_id,'REBUILD_REPORT','SUCCEEDED',"
                        "'same-client-key',:actor_id,'{}',now())"
                    ),
                    {"actor_id": actor_a, "hospital_id": hospital_a},
                )
    finally:
        # Then: the scoped replay is rejected and every QA row is removed
        with engine.begin() as connection:
            connection.execute(cleanup, parameters)
            assert connection.execute(
                sa.text(
                    "SELECT count(*) FROM operation_runs WHERE idempotency_key='same-client-key'"
                )
            ).scalar_one() == 0
        engine.dispose()
