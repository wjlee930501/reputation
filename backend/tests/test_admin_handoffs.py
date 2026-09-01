import uuid
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api.admin import handoffs as handoffs_api
from app.models.admin_user import AdminUser
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, Plan
from app.schemas.handoff import HandoffAccept, HandoffContract
from app.services.admin_passwords import hash_admin_password


def test_stale_conflict_has_reload_guidance() -> None:
    error = handoffs_api.stale_handoff_error()

    assert error.status_code == 409
    assert error.detail["code"] == "HANDOFF_VERSION_CONFLICT"
    assert error.detail["reload"] is True


class RejectingDB:
    def __init__(self, handoff: HospitalHandoff, accounts: list[AdminUser]):
        self.handoff = handoff
        self.accounts = {account.id: account for account in accounts}

    async def get(self, model, object_id):
        if model is HospitalHandoff and object_id == self.handoff.id:
            return self.handoff
        if model is AdminUser:
            return self.accounts.get(object_id)
        return None


class MemoryDB(RejectingDB):
    def __init__(self, handoff: HospitalHandoff, accounts: list[AdminUser]):
        super().__init__(handoff, accounts)
        self.hospital = Hospital(id=handoff.hospital_id, name="QA", slug=f"qa-{uuid.uuid4()}")
        self.added = []

    async def get(self, model, object_id):
        if model is Hospital and object_id == self.hospital.id:
            return self.hospital
        return await super().get(model, object_id)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.handoff.version += 1

    async def rollback(self):
        return None

    async def refresh(self, _item):
        return None


def _account(role: str) -> AdminUser:
    return AdminUser(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.test",
        name=role,
        role=role,
        password_hash=hash_admin_password("correct horse battery staple"),
        is_active=True,
    )


def _contracted(ae: AdminUser) -> HospitalHandoff:
    handoff = HospitalHandoff(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        state=HandoffState.CONTRACTED,
        sales_owner_id=ae.id,
        ae_owner_id=ae.id,
        contract_reference="CTR-1",
        contract_effective_at=datetime.now(UTC),
        plan=Plan.PLAN_12,
        sla_due_at=datetime.now(UTC),
        acceptance_source=HandoffSource.DIRECT_CREATE,
        version=2,
    )
    return handoff


def _pending(operator: AdminUser) -> HospitalHandoff:
    handoff = HospitalHandoff.pending(
        uuid.uuid4(),
        sales_owner_id=operator.id,
        ae_owner_id=operator.id,
        source=HandoffSource.DIRECT_CREATE,
    )
    handoff.id = uuid.uuid4()
    handoff.version = 1
    handoff.created_at = datetime.now(UTC)
    handoff.updated_at = datetime.now(UTC)
    return handoff


async def test_assigned_operator_records_contract_then_accepts() -> None:
    actor = _account("OPERATOR")
    handoff = _pending(actor)
    db = MemoryDB(handoff, [actor])
    contract = HandoffContract(
        version=1,
        contract_reference="CTR-1",
        contract_effective_at=datetime.now(UTC),
        plan=Plan.PLAN_12,
        sla_due_at=datetime.now(UTC),
    )

    contracted = await handoffs_api.contract_handoff(handoff.id, contract, db=db, actor=actor)
    accepted = await handoffs_api.accept_handoff(
        handoff.id, HandoffAccept(version=contracted["version"]), db=db, actor=actor
    )

    assert accepted["state"] is HandoffState.HANDOFF_ACCEPTED
    assert accepted["accepted_by_id"] == actor.id
    assert accepted["accepted_at"] is not None


async def test_unassigned_operator_cannot_record_contract() -> None:
    assigned = _account("OPERATOR")
    actor = _account("OPERATOR")
    handoff = _pending(assigned)
    contract = HandoffContract(
        version=1,
        contract_reference="CTR-1",
        contract_effective_at=datetime.now(UTC),
        plan=Plan.PLAN_12,
        sla_due_at=datetime.now(UTC),
    )

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.contract_handoff(
            handoff.id, contract, db=MemoryDB(handoff, [assigned, actor]), actor=actor
        )

    assert exc.value.status_code == 403


async def test_owner_accepts_for_another_ae_with_reason() -> None:
    assigned = _account("OPERATOR")
    actor = _account("OWNER")
    handoff = _contracted(assigned)
    handoff.created_at = datetime.now(UTC)
    handoff.updated_at = datetime.now(UTC)

    accepted = await handoffs_api.accept_handoff(
        handoff.id,
        HandoffAccept(version=2, reason="AE 휴가로 긴급 인수"),
        db=MemoryDB(handoff, [assigned, actor]),
        actor=actor,
    )

    assert accepted["accepted_by_id"] == actor.id


async def test_contract_correction_is_owner_only() -> None:
    actor = _account("OPERATOR")
    handoff = _contracted(actor)
    with pytest.raises(HTTPException) as exc:
        await handoffs_api.correct_contract(
            handoff.id,
            handoffs_api.HandoffCorrection(
                version=2,
                reason="요금제 정정",
                contract_reference="CTR-2",
                contract_effective_at=datetime.now(UTC),
                plan=Plan.PLAN_20,
                sla_due_at=datetime.now(UTC),
            ),
            db=MemoryDB(handoff, [actor]),
            actor=actor,
        )

    assert exc.value.status_code == 403


async def test_owner_correction_updates_handoff_and_hospital_plan_with_audit_reason() -> None:
    actor = _account("OWNER")
    handoff = _contracted(actor)
    handoff.created_at = datetime.now(UTC)
    handoff.updated_at = datetime.now(UTC)
    db = MemoryDB(handoff, [actor])

    corrected = await handoffs_api.correct_contract(
        handoff.id,
        handoffs_api.HandoffCorrection(
            version=2,
            reason="계약서 요금제 오기 정정",
            contract_reference="CTR-2",
            contract_effective_at=datetime.now(UTC),
            plan=Plan.PLAN_20,
            sla_due_at=datetime.now(UTC),
        ),
        db=db,
        actor=actor,
    )

    assert corrected["plan"] is Plan.PLAN_20
    assert db.hospital.plan is Plan.PLAN_20
    audit = next(item for item in db.added if item.action == "handoff_contract_corrected")
    assert audit.detail["reason"] == "계약서 요금제 오기 정정"


async def test_operator_cannot_accept_another_ae_assignment() -> None:
    assigned = _account("OPERATOR")
    actor = _account("OPERATOR")
    handoff = _contracted(assigned)

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.accept_handoff(
            handoff.id,
            HandoffAccept(version=2),
            db=RejectingDB(handoff, [assigned, actor]),
            actor=actor,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "HANDOFF_NOT_ASSIGNED"


async def test_owner_accepting_for_ae_requires_reason() -> None:
    assigned = _account("OPERATOR")
    actor = _account("OWNER")
    handoff = _contracted(assigned)

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.accept_handoff(
            handoff.id,
            HandoffAccept(version=2),
            db=RejectingDB(handoff, [assigned, actor]),
            actor=actor,
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "OWNER_OVERRIDE_REASON_REQUIRED"


async def test_acceptance_rejects_stale_version_before_mutation() -> None:
    actor = _account("OPERATOR")
    handoff = _contracted(actor)

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.accept_handoff(
            handoff.id, HandoffAccept(version=1), db=RejectingDB(handoff, [actor]), actor=actor
        )

    assert exc.value.status_code == 409
    assert handoff.accepted_at is None


class ListDB:
    """Fake session for list_handoffs — filters an in-memory list the same way the
    real WHERE clauses would, by inspecting the compiled statement text. Avoids
    needing a live database just to exercise the query-building branches."""

    def __init__(self, handoffs: list[HospitalHandoff]):
        self.handoffs = handoffs

    async def get(self, model, object_id):
        return None

    async def execute(self, stmt):
        rendered = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        rows = list(self.handoffs)
        if "hospital_handoffs.hospital_id = " in rendered:
            rows = [h for h in rows if h.hospital_id.hex in rendered]
        if "hospital_handoffs.state = " in rendered:
            rows = [h for h in rows if f"'{h.state.value}'" in rendered]
        result = Mock()
        result.scalars.return_value.all.return_value = rows
        return result


def _pending_for(hospital_id: uuid.UUID, operator: AdminUser) -> HospitalHandoff:
    handoff = HospitalHandoff.pending(
        hospital_id,
        sales_owner_id=operator.id,
        ae_owner_id=operator.id,
        source=HandoffSource.DIRECT_CREATE,
    )
    handoff.id = uuid.uuid4()
    handoff.version = 1
    handoff.created_at = datetime.now(UTC)
    handoff.updated_at = datetime.now(UTC)
    return handoff


async def test_list_handoffs_filters_by_hospital_id() -> None:
    operator = _account("OPERATOR")
    target_hospital_id = uuid.uuid4()
    matching = _pending_for(target_hospital_id, operator)
    other = _pending_for(uuid.uuid4(), operator)
    db = ListDB([matching, other])

    rows = await handoffs_api.list_handoffs(
        state=None, hospital_id=target_hospital_id, db=db, _actor=operator
    )

    assert [row["id"] for row in rows] == [matching.id]


async def test_list_handoffs_without_hospital_id_returns_all() -> None:
    operator = _account("OPERATOR")
    matching = _pending_for(uuid.uuid4(), operator)
    other = _pending_for(uuid.uuid4(), operator)
    db = ListDB([matching, other])

    rows = await handoffs_api.list_handoffs(state=None, hospital_id=None, db=db, _actor=operator)

    assert {row["id"] for row in rows} == {matching.id, other.id}
