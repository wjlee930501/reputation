import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models import HandoffSource, HandoffState, HospitalHandoff
from app.schemas.handoff import HandoffAccept, HandoffContract, HandoffPendingCreate


def _sqlite_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE hospitals (id CHAR(32) PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE admin_users (id CHAR(32) PRIMARY KEY)"))
    HospitalHandoff.__table__.create(engine)
    return engine


def test_pending_constructor_defaults_new_hospital_to_contract_pending() -> None:
    # Given: a newly allocated hospital identifier
    hospital_id = uuid.uuid4()
    sales_owner_id = uuid.uuid4()
    ae_owner_id = uuid.uuid4()

    # When: its handoff is constructed for atomic persistence
    handoff = HospitalHandoff.pending(
        hospital_id,
        sales_owner_id=sales_owner_id,
        ae_owner_id=ae_owner_id,
    )

    # Then: no operational acceptance is fabricated
    assert (
        handoff.hospital_id,
        handoff.state,
        handoff.acceptance_source,
        handoff.sales_owner_id,
        handoff.ae_owner_id,
        handoff.accepted_by_id,
        handoff.accepted_at,
    ) == (
        hospital_id,
        HandoffState.CONTRACT_PENDING,
        HandoffSource.DIRECT_CREATE,
        sales_owner_id,
        ae_owner_id,
        None,
        None,
    )


def test_hospital_id_is_unique_and_version_is_mapper_controlled() -> None:
    # Given: the mapped handoff table and mapper
    unique_constraints = {
        tuple(constraint.columns.keys())
        for constraint in HospitalHandoff.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }

    # When: the concurrency and cardinality contracts are inspected
    version_column = HospitalHandoff.__mapper__.version_id_col

    # Then: one handoff exists per hospital and updates compare the integer version
    assert ("hospital_id",) in unique_constraints
    assert version_column is HospitalHandoff.__table__.c.version
    assert HospitalHandoff.__table__.c.version.nullable is False


def test_contract_schema_rejects_missing_owner_contract_and_sla_facts() -> None:
    # Given: a contracted transition with only a version
    incomplete_transition = {"version": 1}

    # When: the API boundary parses it
    with pytest.raises(ValidationError) as error:
        HandoffContract.model_validate(incomplete_transition)

    # Then: every required commercial fact is rejected as missing
    missing_fields = {tuple(item["loc"]) for item in error.value.errors()}
    assert missing_fields == {
        ("contract_reference",),
        ("contract_effective_at",),
        ("plan",),
        ("sla_due_at",),
    }


def test_pending_schema_is_immutable_boundary_data() -> None:
    # Given: a parsed pending handoff request
    request = HandoffPendingCreate(
        sales_owner_id=uuid.uuid4(),
        ae_owner_id=uuid.uuid4(),
    )

    # When: downstream code tries to mutate it
    with pytest.raises(ValidationError):
        request.sales_owner_id = uuid.uuid4()

    # Then: the original owner remains intact
    assert request.sales_owner_id is not None


def test_contract_schema_rejects_naive_effective_and_sla_datetimes() -> None:
    # Given: contract facts whose timestamps omit an offset
    payload = {
        "version": 1,
        "contract_reference": "CTR-20260810",
        "contract_effective_at": "2026-08-10T09:00:00",
        "plan": "PLAN_12",
        "sla_due_at": "2026-08-11T18:00:00",
    }

    # When: the API boundary parses them
    with pytest.raises(ValidationError) as error:
        HandoffContract.model_validate(payload)

    # Then: both environment-dependent instants are rejected
    rejected_fields = {tuple(item["loc"]) for item in error.value.errors()}
    assert rejected_fields == {("contract_effective_at",), ("sla_due_at",)}


def test_acceptance_request_does_not_trust_client_actor_or_time() -> None:
    # Given: a client attempts to supply server-owned acceptance facts
    payload = {
        "version": 2,
        "accepted_by_id": str(uuid.uuid4()),
        "accepted_at": "2026-08-10T10:00:00+09:00",
    }

    # When: the strict acceptance boundary parses it
    with pytest.raises(ValidationError):
        HandoffAccept.model_validate(payload)

    # Then: only compare-and-swap data can cross this boundary
    assert set(HandoffAccept.model_fields) <= {"version", "reason"}


def test_accepted_actor_and_time_are_immutable_once_set() -> None:
    # Given: an accepted handoff with durable actor/time facts
    accepted_at = datetime(2026, 8, 10, tzinfo=UTC)
    handoff = HospitalHandoff.pending(
        uuid.uuid4(),
        sales_owner_id=uuid.uuid4(),
        ae_owner_id=uuid.uuid4(),
    )
    accepted_by_id = uuid.uuid4()
    handoff.accepted_by_id = accepted_by_id
    handoff.accepted_at = accepted_at

    # When: either accepted fact is changed
    with pytest.raises(ValueError, match="immutable"):
        handoff.accepted_by_id = uuid.uuid4()

    # Then: the originally accepted facts remain unchanged
    assert (handoff.accepted_by_id, handoff.accepted_at) == (accepted_by_id, accepted_at)


def test_second_concurrent_update_raises_stale_data_error() -> None:
    # Given: two sessions loaded the same pending handoff version
    engine = _sqlite_engine()
    hospital_id = uuid.uuid4()
    handoff_id = uuid.uuid4()
    sales_owner_id = uuid.uuid4()
    ae_owner_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO hospitals (id) VALUES (:id)"), {"id": hospital_id.hex}
        )
        connection.execute(
            sa.text("INSERT INTO admin_users (id) VALUES (:id)"),
            [{"id": sales_owner_id.hex}, {"id": ae_owner_id.hex}],
        )
    with Session(engine) as setup_session:
        handoff = HospitalHandoff.pending(
            hospital_id,
            sales_owner_id=sales_owner_id,
            ae_owner_id=ae_owner_id,
        )
        handoff.id = handoff_id
        setup_session.add(handoff)
        setup_session.commit()
    first_session = Session(engine)
    second_session = Session(engine)
    first = first_session.get(HospitalHandoff, handoff_id)
    second = second_session.get(HospitalHandoff, handoff_id)
    assert first is not None and second is not None

    # When: the first writer commits and the stale writer flushes its old version
    first.acceptance_source = HandoffSource.LEAD_CONVERSION
    first_session.commit()
    second.acceptance_source = HandoffSource.LEAD_CONVERSION
    with pytest.raises(StaleDataError):
        second_session.flush()

    # Then: only the first update is durable
    second_session.rollback()
    with Session(engine) as verification_session:
        persisted = verification_session.get(HospitalHandoff, handoff_id)
        assert persisted is not None
        assert isinstance(persisted.state, HandoffState)
        assert isinstance(persisted.acceptance_source, HandoffSource)
        assert (persisted.acceptance_source, persisted.version) == (
            HandoffSource.LEAD_CONVERSION,
            2,
        )
    first_session.close()
    second_session.close()
    engine.dispose()
