import uuid
from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Response

from app.api.admin import handoffs as handoffs_api
from app.models.admin_user import AdminUser
from app.models.content import ContentSchedule
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, Plan
from app.models.lead import SalesLead
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


class _ScalarResult:
    def __init__(self, rows: list):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class MemoryDB(RejectingDB):
    def __init__(
        self,
        handoff: HospitalHandoff,
        accounts: list[AdminUser],
        schedules: list[ContentSchedule] | None = None,
    ):
        super().__init__(handoff, accounts)
        self.hospital = Hospital(id=handoff.hospital_id, name="QA", slug=f"qa-{uuid.uuid4()}")
        self.added = []
        # 활성 발행 일정 조회는 이 목록만 돌려준다 — 계약 정정이 일정 요금제를 맞추는지만 본다.
        self.schedules = schedules or []

    async def get(self, model, object_id):
        if model is Hospital and object_id == self.hospital.id:
            return self.hospital
        return await super().get(model, object_id)

    async def execute(self, _stmt):
        return _ScalarResult([s for s in self.schedules if s.is_active])

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
    # 일정이 없으면 동기화 기록도 남기지 않는다.
    assert "schedule_plan_synced" not in audit.detail


async def test_owner_correction_syncs_the_active_schedule_plan_with_audit() -> None:
    actor = _account("OWNER")
    handoff = _contracted(actor)
    handoff.created_at = datetime.now(UTC)
    handoff.updated_at = datetime.now(UTC)
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=handoff.hospital_id,
        plan="PLAN_12",
        publish_days=[1, 4],
        active_from=date(2026, 9, 1),
        is_active=True,
    )
    retired = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=handoff.hospital_id,
        plan="PLAN_12",
        publish_days=[1],
        active_from=date(2026, 1, 1),
        is_active=False,
    )
    db = MemoryDB(handoff, [actor], schedules=[schedule, retired])

    await handoffs_api.correct_contract(
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

    # 월 약정 편수는 ContentSchedule.plan을 읽는다 — 정정이 여기까지 오지 않으면
    # 다음 달도 옛 편수로 슬롯이 생긴다.
    assert schedule.plan == "PLAN_20"
    assert retired.plan == "PLAN_12"
    audit = next(item for item in db.added if item.action == "handoff_contract_corrected")
    assert audit.detail["schedule_plan_synced"] == [
        {"schedule_id": str(schedule.id), "from": "PLAN_12", "to": "PLAN_20"}
    ]


async def test_contract_record_syncs_an_existing_active_schedule_plan() -> None:
    actor = _account("OPERATOR")
    handoff = _pending(actor)
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=handoff.hospital_id,
        plan="PLAN_12",
        publish_days=[1, 4],
        active_from=date(2026, 9, 1),
        is_active=True,
    )
    db = MemoryDB(handoff, [actor], schedules=[schedule])

    await handoffs_api.contract_handoff(
        handoff.id,
        HandoffContract(
            version=1,
            contract_reference="CTR-1",
            contract_effective_at=datetime.now(UTC),
            plan=Plan.PLAN_16,
            sla_due_at=datetime.now(UTC),
        ),
        db=db,
        actor=actor,
    )

    assert schedule.plan == "PLAN_16"
    audit = next(item for item in db.added if item.action == "handoff_contracted")
    assert audit.detail["schedule_plan_synced"][0]["to"] == "PLAN_16"


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


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class BatchListDB:
    """Fakes the exact `db.execute` sequence `list_handoffs` issues: the page query,
    then (only if non-empty) one Hospital IN(...) and one AdminUser IN(...) query."""

    def __init__(self, *, rows, hospitals=(), users=()):
        self.rows = rows
        self.hospitals = list(hospitals)
        self.users = list(users)
        self.calls: list[str] = []

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is HospitalHandoff:
            self.calls.append("handoffs")
            return _ScalarResult(self.rows)
        if entity is Hospital:
            self.calls.append("hospitals")
            return _ScalarResult(self.hospitals)
        if entity is AdminUser:
            self.calls.append("users")
            return _ScalarResult(self.users)
        raise AssertionError(f"unexpected entity {entity}")


async def test_payloads_batch_resolves_names_without_a_query_per_row() -> None:
    ae = _account("OPERATOR")
    sales = _account("OPERATOR")
    hospital = Hospital(id=uuid.uuid4(), name="강남 병원", slug="gangnam")
    handoff_a = _pending(ae)
    handoff_a.hospital_id = hospital.id
    handoff_a.sales_owner_id = sales.id
    handoff_b = _pending(ae)
    handoff_b.hospital_id = hospital.id
    handoff_b.sales_owner_id = ae.id  # 같은 담당자를 두 행이 공유 — IN(...)엔 한 번만 담긴다

    db = BatchListDB(rows=[handoff_a, handoff_b], hospitals=[hospital], users=[ae, sales])

    payloads = await handoffs_api._payloads_batch(db, [handoff_a, handoff_b])

    assert db.calls == ["hospitals", "users"]  # 행 수와 무관하게 딱 2번
    assert payloads[0]["hospital_name"] == "강남 병원"
    assert payloads[0]["sales_owner_name"] == sales.name
    assert payloads[1]["hospital_name"] == "강남 병원"
    assert payloads[1]["sales_owner_name"] == ae.name


async def test_payloads_batch_skips_the_user_query_when_no_owner_is_set() -> None:
    """hospital_id는 스키마상 필수라 병원 조회는 항상 돌지만, sales/ae/accepted가
    전부 비어 있으면(초기 상태 등) admin_users IN(...) 조회는 아예 나가지 않아야 한다."""
    operator = _account("OPERATOR")
    handoff = _pending(operator)
    handoff.sales_owner_id = None
    handoff.ae_owner_id = None

    db = BatchListDB(rows=[handoff])  # hospitals=() — 매칭 병원 없음

    payloads = await handoffs_api._payloads_batch(db, [handoff])

    assert db.calls == ["hospitals"]  # user_ids가 비어 있으니 admin_users 조회는 스킵
    assert payloads[0]["hospital_name"] is None
    assert payloads[0]["sales_owner_name"] is None


async def test_list_handoffs_applies_limit_and_batches_lookups() -> None:
    ae = _account("OPERATOR")
    hospital = Hospital(id=uuid.uuid4(), name="서초 병원", slug="seocho")
    handoff = _pending(ae)
    handoff.hospital_id = hospital.id
    handoff.sales_owner_id = ae.id

    db = BatchListDB(rows=[handoff], hospitals=[hospital], users=[ae])

    response = Response()
    result = await handoffs_api.list_handoffs(
        response, state=None, limit=5, offset=0, db=db, _actor=ae
    )

    assert db.calls == ["handoffs", "hospitals", "users"]
    assert len(result) == 1
    assert result[0]["hospital_name"] == "서초 병원"


class ListDB:
    """Fake session for list_handoffs — filters an in-memory list the same way the
    real WHERE clauses would, by inspecting the compiled statement text. Avoids
    needing a live database just to exercise the query-building branches."""

    def __init__(self, handoffs: list[HospitalHandoff]):
        self.handoffs = handoffs

    async def get(self, model, object_id):
        return None

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is not HospitalHandoff:
            # 배치 이름 조회(Hospital/AdminUser IN(...))는 이 테스트의 관심사가 아니다.
            result = Mock()
            result.scalars.return_value.all.return_value = []
            return result
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
        Response(),
        state=None,
        hospital_id=target_hospital_id,
        limit=100,
        offset=0,
        db=db,
        _actor=operator,
    )

    assert [row["id"] for row in rows] == [matching.id]


async def test_list_handoffs_without_hospital_id_returns_all() -> None:
    operator = _account("OPERATOR")
    matching = _pending_for(uuid.uuid4(), operator)
    other = _pending_for(uuid.uuid4(), operator)
    db = ListDB([matching, other])

    rows = await handoffs_api.list_handoffs(
        Response(), state=None, hospital_id=None, limit=100, offset=0, db=db, _actor=operator
    )

    assert {row["id"] for row in rows} == {matching.id, other.id}


# ── 목록 페이지네이션: 조용한 잘림을 없앤다 ──────────────────────────────


class _PagingDB(ListDB):
    """SQL의 OFFSET/LIMIT을 in-memory 슬라이스로 그대로 재현한다."""

    def __init__(self, handoffs: list[HospitalHandoff]):
        super().__init__(handoffs)
        self.limits: list[int] = []
        self.offsets: list[int] = []

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is not HospitalHandoff:
            return await super().execute(stmt)
        limit = stmt._limit_clause.value
        offset = stmt._offset_clause.value if stmt._offset_clause is not None else 0
        self.limits.append(limit)
        self.offsets.append(offset)
        result = Mock()
        result.scalars.return_value.all.return_value = self.handoffs[offset : offset + limit]
        return result


def _pending_rows(count: int, operator: AdminUser) -> list[HospitalHandoff]:
    return [_pending_for(uuid.uuid4(), operator) for _ in range(count)]


async def test_list_handoffs_reports_has_more_when_truncated() -> None:
    operator = _account("OPERATOR")
    db = _PagingDB(_pending_rows(5, operator))
    response = Response()

    rows = await handoffs_api.list_handoffs(
        response, state=None, limit=2, offset=0, db=db, _actor=operator
    )

    assert len(rows) == 2
    # 잘림 판정을 위해 limit+1건을 읽는다.
    assert db.limits == [3] and db.offsets == [0]
    assert response.headers[handoffs_api.HAS_MORE_HEADER] == "true"
    assert response.headers[handoffs_api.NEXT_OFFSET_HEADER] == "2"


async def test_list_handoffs_offset_moves_the_window_and_ends_cleanly() -> None:
    operator = _account("OPERATOR")
    handoffs = _pending_rows(5, operator)
    db = _PagingDB(handoffs)
    response = Response()

    rows = await handoffs_api.list_handoffs(
        response, state=None, limit=2, offset=4, db=db, _actor=operator
    )

    assert [row["id"] for row in rows] == [handoffs[4].id]
    assert db.offsets == [4]
    assert response.headers[handoffs_api.HAS_MORE_HEADER] == "false"
    assert response.headers[handoffs_api.NEXT_OFFSET_HEADER] == "5"


def test_list_handoffs_offset_query_parameter_defaults_to_zero() -> None:
    """직접 호출 테스트는 의존성 해석을 거치지 않으므로 선언 자체를 확인한다."""
    import inspect

    signature = inspect.signature(handoffs_api.list_handoffs)
    offset_default = signature.parameters["offset"].default
    assert offset_default.default == 0
    assert offset_default.metadata[0].ge == 0


# ── 한 화면 계약 등록 (POST /admin/hospitals/register-contract) ────────────
class _RegisterResult:
    def __init__(self, rows: list, single=None):
        self._rows = rows
        self._single = single

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._single


class _RegisterDB:
    """계약 등록이 만지는 것만 흉내 낸다 — 중복 조회·slug 조회·flush·commit/rollback."""

    def __init__(
        self,
        accounts: list[AdminUser],
        *,
        duplicates: list[Hospital] | None = None,
        reference_rows: list[HospitalHandoff] | None = None,
        lead: SalesLead | None = None,
    ):
        self.accounts = {account.id: account for account in accounts}
        self.duplicates = duplicates or []
        self.reference_rows = reference_rows or []
        self.lead = lead
        self.added: list = []
        self.committed = False
        self.rolled_back = False

    async def get(self, model, object_id):
        if model is AdminUser:
            return self.accounts.get(object_id)
        return None

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is SalesLead:
            return _RegisterResult([], single=self.lead)
        if entity is HospitalHandoff:
            return _RegisterResult(self.reference_rows)
        # 같은 Hospital select라도 중복 검사는 scalars().all(), slug 검사는
        # scalar_one_or_none()을 쓴다 — 한 결과 객체가 둘 다 답한다.
        return _RegisterResult(self.duplicates, single=None)

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        # 실제 INSERT가 채우는 기본 PK를 흉내 낸다.
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, _item):
        return None


def _registration(ae: AdminUser, sales: AdminUser | None = None, **overrides):
    from app.schemas.handoff import ContractRegistration

    payload = {
        "name": "장편한외과의원",
        "contract_reference": "RP-202609-a1b2",
        "contract_effective_at": date(2026, 9, 9),
        "plan": Plan.PLAN_16,
        "ae_owner_id": ae.id,
        "sales_owner_id": sales.id if sales else None,
    }
    payload.update(overrides)
    return ContractRegistration(**payload)


@pytest.fixture
def _verified_actor():
    from app.services import audit_log

    token = audit_log.set_request_actor("ae@example.test")
    try:
        yield "ae@example.test"
    finally:
        audit_log.reset_request_actor(token)


def _audit_actions(db: _RegisterDB) -> list[str]:
    return [getattr(row, "action", None) for row in db.added if getattr(row, "action", None)]


async def test_register_contract_creates_hospital_and_accepts_in_one_commit(
    _verified_actor,
) -> None:
    ae = _account("OPERATOR")
    sales = _account("OPERATOR")
    db = _RegisterDB([ae, sales])

    response = await handoffs_api.register_contract(
        _registration(ae, sales), db=db, actor=ae
    )

    assert db.committed is True and db.rolled_back is False
    hospital = next(row for row in db.added if isinstance(row, Hospital))
    handoff = next(row for row in db.added if isinstance(row, HospitalHandoff))
    assert handoff.hospital_id == hospital.id
    assert handoff.state is HandoffState.HANDOFF_ACCEPTED
    assert handoff.acceptance_source is HandoffSource.DIRECT_CREATE
    assert handoff.contract_reference == "RP-202609-a1b2"
    assert handoff.plan is Plan.PLAN_16
    assert handoff.ae_owner_id == ae.id and handoff.sales_owner_id == sales.id
    assert handoff.accepted_by_id == ae.id
    # 인수 처리 기한은 승인 시각 그대로다 — 이 요청에서 인수까지 끝났다.
    assert handoff.sla_due_at == handoff.accepted_at
    assert handoff.contract_effective_at.date() == date(2026, 9, 9)
    assert _audit_actions(db) == ["create_hospital", "handoff_contracted", "handoff_accepted"]
    assert all(row.actor == "ae@example.test" for row in db.added if hasattr(row, "action"))
    assert response["handoff"]["state"] is HandoffState.HANDOFF_ACCEPTED
    assert response["id"] == str(hospital.id)


def _lead(**overrides) -> SalesLead:
    values = {
        "id": uuid.uuid4(),
        "clinic_name": "장편한외과의원",
        "clinic_type": "외과 / 서울",
        "contact": "010-0000-0000",
        "status": "NEW",
    }
    values.update(overrides)
    return SalesLead(**values)


async def test_register_contract_links_the_lead_and_records_the_conversion_source(
    _verified_actor,
) -> None:
    ae = _account("OPERATOR")
    lead = _lead()
    db = _RegisterDB([ae], lead=lead)

    await handoffs_api.register_contract(
        _registration(ae, lead_id=lead.id), db=db, actor=ae
    )

    hospital = next(row for row in db.added if isinstance(row, Hospital))
    handoff = next(row for row in db.added if isinstance(row, HospitalHandoff))
    assert hospital.source_lead_id == lead.id
    assert handoff.acceptance_source is HandoffSource.LEAD_CONVERSION
    # 영업 담당이 비어 있으면 등록한 운영자가 맡는다.
    assert handoff.sales_owner_id == ae.id


async def test_register_contract_converts_the_lead_in_the_same_transaction(
    _verified_actor,
) -> None:
    """리드가 NEW로 남으면 상담 요청 목록이 같은 계약을 다시 등록하라고 권한다."""
    ae = _account("OPERATOR")
    lead = _lead()
    db = _RegisterDB([ae], lead=lead)

    await handoffs_api.register_contract(
        _registration(ae, lead_id=lead.id), db=db, actor=ae
    )

    hospital = next(row for row in db.added if isinstance(row, Hospital))
    assert lead.status == "CONVERTED"
    assert lead.converted_hospital_id == hospital.id
    assert lead.converted_at is not None
    assert f"Source lead: {lead.id}" in (lead.conversion_note or "")
    assert _audit_actions(db) == [
        "create_hospital",
        "handoff_contracted",
        "handoff_accepted",
        "convert_sales_lead",
    ]
    assert db.committed is True


async def test_register_contract_refuses_a_lead_that_already_has_a_hospital(
    _verified_actor,
) -> None:
    ae = _account("OPERATOR")
    other_hospital_id = uuid.uuid4()
    lead = _lead(status="CONVERTED", converted_hospital_id=other_hospital_id)
    db = _RegisterDB([ae], lead=lead)

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.register_contract(
            _registration(ae, lead_id=lead.id), db=db, actor=ae
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "LEAD_ALREADY_CONVERTED"
    assert exc.value.detail["hospital_id"] == str(other_hospital_id)
    assert db.added == [] and db.committed is False


async def test_register_contract_names_a_missing_lead_instead_of_a_duplicate_hospital(
    _verified_actor,
) -> None:
    """지워진 상담 요청은 외래키 위반으로 흘러 "이미 등록된 병원"으로 답하곤 했다."""
    ae = _account("OPERATOR")
    db = _RegisterDB([ae])

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.register_contract(
            _registration(ae, lead_id=uuid.uuid4()), db=db, actor=ae
        )

    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "LEAD_NOT_FOUND"
    assert db.added == [] and db.committed is False


async def test_register_contract_rolls_back_the_lead_conversion_with_the_hospital(
    monkeypatch, _verified_actor
) -> None:
    ae = _account("OPERATOR")
    lead = _lead()
    db = _RegisterDB([ae], lead=lead)

    async def _boom(*_args, **kwargs):
        if kwargs.get("action") == "convert_sales_lead":
            raise RuntimeError("리드 전환 기록 실패")
        return None

    monkeypatch.setattr(handoffs_api, "write_audit_log", _boom)

    with pytest.raises(RuntimeError):
        await handoffs_api.register_contract(
            _registration(ae, lead_id=lead.id), db=db, actor=ae
        )

    assert db.committed is False and db.rolled_back is True


async def test_register_contract_refuses_a_duplicate_hospital_without_creating_rows(
    _verified_actor,
) -> None:
    ae = _account("OPERATOR")
    existing = Hospital(id=uuid.uuid4(), name="장편한외과의원", slug="jang")
    db = _RegisterDB([ae], duplicates=[existing])

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.register_contract(_registration(ae), db=db, actor=ae)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "HOSPITAL_EXISTS"
    assert exc.value.detail["hospital_id"] == str(existing.id)
    assert db.added == [] and db.committed is False


async def test_register_contract_refuses_a_reused_contract_reference(_verified_actor) -> None:
    ae = _account("OPERATOR")
    other = HospitalHandoff(
        id=uuid.uuid4(), hospital_id=uuid.uuid4(), contract_reference="RP-202609-a1b2"
    )
    db = _RegisterDB([ae], reference_rows=[other])

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.register_contract(_registration(ae), db=db, actor=ae)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "CONTRACT_REFERENCE_EXISTS"
    assert db.added == [] and db.committed is False


async def test_register_contract_rejects_an_invalid_plan_or_empty_reference() -> None:
    from pydantic import ValidationError

    ae = _account("OPERATOR")
    # 공백만 넣은 값도 빈 값이다 — 이름 없는 병원과 빈 계약 번호를 만들지 않는다.
    for overrides in (
        {"plan": "PLAN_9"},
        {"contract_reference": ""},
        {"contract_reference": "   "},
        {"name": " \t "},
    ):
        with pytest.raises(ValidationError):
            _registration(ae, **overrides)


async def test_register_contract_stores_trimmed_name_and_reference(_verified_actor) -> None:
    ae = _account("OPERATOR")
    db = _RegisterDB([ae])

    await handoffs_api.register_contract(
        _registration(ae, name="  장편한외과의원  ", contract_reference=" RP-202609-a1b2 "),
        db=db,
        actor=ae,
    )

    hospital = next(row for row in db.added if isinstance(row, Hospital))
    handoff = next(row for row in db.added if isinstance(row, HospitalHandoff))
    assert hospital.name == "장편한외과의원"
    assert handoff.contract_reference == "RP-202609-a1b2"


async def test_register_contract_rolls_everything_back_when_the_contract_step_fails(
    monkeypatch, _verified_actor
) -> None:
    ae = _account("OPERATOR")
    db = _RegisterDB([ae])

    async def _boom(*_args, **kwargs):
        if kwargs.get("action") == "handoff_contracted":
            raise RuntimeError("계약 기록 실패")
        return None

    monkeypatch.setattr(handoffs_api, "write_audit_log", _boom)

    with pytest.raises(RuntimeError):
        await handoffs_api.register_contract(_registration(ae), db=db, actor=ae)

    assert db.committed is False and db.rolled_back is True


async def test_register_contract_blocks_an_operator_accepting_for_another_ae(
    _verified_actor,
) -> None:
    actor = _account("OPERATOR")
    other_ae = _account("OPERATOR")
    db = _RegisterDB([actor, other_ae])

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.register_contract(_registration(other_ae), db=db, actor=actor)

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "HANDOFF_NOT_ASSIGNED"
    assert db.added == []


async def test_register_contract_lets_an_owner_register_for_another_ae_with_an_audited_override(
    _verified_actor,
) -> None:
    owner = _account("OWNER")
    ae = _account("OPERATOR")
    db = _RegisterDB([owner, ae])

    await handoffs_api.register_contract(_registration(ae), db=db, actor=owner)

    accepted = next(row for row in db.added if getattr(row, "action", None) == "handoff_accepted")
    assert accepted.detail["owner_override"] is True


async def test_register_contract_requires_a_verified_actor() -> None:
    ae = _account("OPERATOR")
    db = _RegisterDB([ae])

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.register_contract(_registration(ae), db=db, actor=ae)

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "VERIFIED_ACTOR_REQUIRED"


async def test_register_contract_refuses_an_inactive_owner(_verified_actor) -> None:
    ae = _account("OPERATOR")
    ae.is_active = False
    db = _RegisterDB([ae])

    with pytest.raises(HTTPException) as exc:
        await handoffs_api.register_contract(_registration(ae), db=db, actor=ae)

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "ACTIVE_OWNER_REQUIRED"
    assert db.added == []
