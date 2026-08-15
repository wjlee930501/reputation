"""#6/#9/#11 — create_hospital 감사 로그 + 경합 409, pause/resume 라이프사이클."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.admin import hospitals as hospitals_api
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.monthly_control import HospitalServiceInterval


# ── create_hospital ──────────────────────────────────────────────
class _CreateDB:
    def __init__(self, existing=None, fail_commit=False):
        self.existing = existing
        self.fail_commit = fail_commit
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.existing)

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        pass

    async def commit(self):
        if self.fail_commit:
            raise IntegrityError("INSERT", {}, Exception("duplicate key value"))
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, item):
        pass


async def test_create_hospital_writes_audit_log():
    db = _CreateDB()
    body = hospitals_api.HospitalCreate(name="장편한외과의원", plan=Plan.PLAN_12)

    response = await hospitals_api.create_hospital(body, db=db)

    assert response["name"] == "장편한외과의원"
    assert db.committed is True
    audit_rows = [a for a in db.added if getattr(a, "action", None) == "create_hospital"]
    assert len(audit_rows) == 1
    assert audit_rows[0].detail["plan"] == "PLAN_12"


async def test_create_hospital_converts_race_integrity_error_to_409():
    db = _CreateDB(fail_commit=True)
    body = hospitals_api.HospitalCreate(name="장편한외과의원")

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.create_hospital(body, db=db)

    assert exc.value.status_code == 409
    assert "슬러그 또는 도메인" in exc.value.detail
    assert db.rolled_back is True
    assert db.committed is False


class _IdempotentCreateDB(_CreateDB):
    async def get(self, model, object_id):
        return next(
            (
                item
                for item in self.added
                if isinstance(item, model) and getattr(item, "id", None) == object_id
            ),
            None,
        )

    async def execute(self, stmt):
        if "hospital_handoffs" in str(stmt):
            handoff = next(
                (item for item in self.added if isinstance(item, HospitalHandoff)),
                None,
            )
            return SimpleNamespace(scalar_one_or_none=lambda: handoff)
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def flush(self):
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()


async def test_create_hospital_replays_same_onboarding_request_without_duplicate():
    db = _IdempotentCreateDB()
    request_id = uuid.uuid4()
    sales_owner_id = uuid.uuid4()
    ae_owner_id = uuid.uuid4()
    body = hospitals_api.HospitalCreate(
        name="재시도 의원",
        plan=Plan.PLAN_16,
        sales_owner_id=sales_owner_id,
        ae_owner_id=ae_owner_id,
        onboarding_request_id=request_id,
    )

    first = await hospitals_api.create_hospital(body, db=db)
    second = await hospitals_api.create_hospital(body, db=db)

    hospitals = [item for item in db.added if item.__class__.__name__ == "Hospital"]
    handoffs = [item for item in db.added if isinstance(item, HospitalHandoff)]
    assert len(hospitals) == 1
    assert len(handoffs) == 1
    assert first["id"] == second["id"] == str(request_id)
    assert first["handoff"]["id"] == second["handoff"]["id"]


async def test_create_hospital_replay_returns_contract_handoff_fields_for_resume():
    db = _IdempotentCreateDB()
    request_id = uuid.uuid4()
    sales_owner_id = uuid.uuid4()
    ae_owner_id = uuid.uuid4()
    body = hospitals_api.HospitalCreate(
        name="재시도 의원",
        plan=Plan.PLAN_16,
        sales_owner_id=sales_owner_id,
        ae_owner_id=ae_owner_id,
        onboarding_request_id=request_id,
    )
    await hospitals_api.create_hospital(body, db=db)
    handoff = next(item for item in db.added if isinstance(item, HospitalHandoff))
    contracted_at = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    sla_due_at = datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)
    handoff.state = HandoffState.CONTRACTED
    handoff.acceptance_source = HandoffSource.DIRECT_CREATE
    handoff.contract_reference = "CTR-DIRECT-1"
    handoff.contract_effective_at = contracted_at
    handoff.plan = Plan.PLAN_16
    handoff.sla_due_at = sla_due_at

    replay = await hospitals_api.create_hospital(body, db=db)

    assert replay["handoff"]["contract_reference"] == "CTR-DIRECT-1"
    assert replay["handoff"]["contract_effective_at"] == contracted_at
    assert replay["handoff"]["plan"] == Plan.PLAN_16
    assert replay["handoff"]["sla_due_at"] == sla_due_at


class _ConcurrentIdempotentCreateDB(_CreateDB):
    def __init__(self, *, prior_hospital, prior_handoff):
        super().__init__()
        self.prior_hospital = prior_hospital
        self.prior_handoff = prior_handoff

    async def get(self, model, object_id):
        if self.rolled_back and model is Hospital and object_id == self.prior_hospital.id:
            return self.prior_hospital
        return None

    async def execute(self, stmt):
        if self.rolled_back and "hospital_handoffs" in str(stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: self.prior_handoff)
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def commit(self):
        raise IntegrityError("INSERT", {}, Exception("duplicate key value"))


async def test_create_hospital_recovers_concurrent_same_onboarding_request():
    request_id = uuid.uuid4()
    sales_owner_id = uuid.uuid4()
    ae_owner_id = uuid.uuid4()
    prior_hospital = Hospital(
        id=request_id,
        name="동시등록의원",
        slug="dongsideungroguiweon",
        plan=Plan.PLAN_20,
    )
    prior_handoff = HospitalHandoff.pending(
        request_id,
        sales_owner_id=sales_owner_id,
        ae_owner_id=ae_owner_id,
        source=HandoffSource.DIRECT_CREATE,
    )
    db = _ConcurrentIdempotentCreateDB(
        prior_hospital=prior_hospital,
        prior_handoff=prior_handoff,
    )
    body = hospitals_api.HospitalCreate(
        name="동시등록의원",
        plan=Plan.PLAN_20,
        sales_owner_id=sales_owner_id,
        ae_owner_id=ae_owner_id,
        onboarding_request_id=request_id,
    )

    response = await hospitals_api.create_hospital(body, db=db)

    assert db.rolled_back is True
    assert response["id"] == str(request_id)
    assert response["handoff"]["id"] == prior_handoff.id


async def test_create_hospital_rejects_concurrent_onboarding_request_payload_mismatch():
    request_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    prior_hospital = Hospital(
        id=request_id,
        name="먼저등록된의원",
        slug="meonjeodeungrogdoenuiweon",
        plan=Plan.PLAN_12,
    )
    prior_handoff = HospitalHandoff.pending(
        request_id,
        sales_owner_id=owner_id,
        ae_owner_id=owner_id,
        source=HandoffSource.DIRECT_CREATE,
    )
    db = _ConcurrentIdempotentCreateDB(
        prior_hospital=prior_hospital,
        prior_handoff=prior_handoff,
    )
    body = hospitals_api.HospitalCreate(
        name="다른의원",
        plan=Plan.PLAN_12,
        sales_owner_id=owner_id,
        ae_owner_id=owner_id,
        onboarding_request_id=request_id,
    )

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.create_hospital(body, db=db)

    assert db.rolled_back is True
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "ONBOARDING_REQUEST_CONFLICT"


# ── pause / resume ───────────────────────────────────────────────
class _LifecycleDB:
    def __init__(self, hospital, *, handoff_state=HandoffState.HANDOFF_ACCEPTED, interval=None):
        self.hospital = hospital
        self.handoff_state = handoff_state
        self.interval = interval
        self.added = []
        self.committed = False

    async def get(self, model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    async def scalar(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is HospitalHandoff:
            return self.handoff_state
        if entity is HospitalServiceInterval:
            return self.interval
        return None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        pass


def _full_hospital(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        status=HospitalStatus.ACTIVE,
        plan=Plan.PLAN_12,
        source_lead_id=None,
        onboarding_note=None,
        address="서울 성동구",
        phone="02-000-0000",
        business_hours=None,
        website_url=None,
        blog_url=None,
        kakao_channel_url=None,
        google_business_profile_url=None,
        google_maps_url=None,
        naver_place_url=None,
        aeo_domain=None,
        latitude=None,
        longitude=None,
        wikidata_qid=None,
        gbp_place_id=None,
        naver_place_id=None,
        kakao_place_id=None,
        hira_org_id=None,
        region=["성동구"],
        specialties=["외과"],
        keywords=["치질"],
        competitors=[],
        director_name="김원장",
        director_career=None,
        director_philosophy=None,
        director_credentials=None,
        treatments=[],
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
        created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("start_status", [HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN])
async def test_pause_from_active_or_pending(start_status):
    hospital = _full_hospital(status=start_status)
    db = _LifecycleDB(hospital)

    response = await hospitals_api.pause_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.PAUSED
    assert response["status"] == HospitalStatus.PAUSED
    assert db.committed is True
    assert [a.action for a in db.added] == ["pause_hospital"]


@pytest.mark.parametrize(
    "start_status",
    [HospitalStatus.ONBOARDING, HospitalStatus.PAUSED, HospitalStatus.BUILDING],
)
async def test_pause_rejected_from_other_states(start_status):
    hospital = _full_hospital(status=start_status)
    db = _LifecycleDB(hospital)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.pause_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert hospital.status == start_status
    assert db.committed is False


async def test_resume_to_active_when_gates_and_site_live_met():
    hospital = _full_hospital(status=HospitalStatus.PAUSED, site_live=True)
    db = _LifecycleDB(hospital)

    response = await hospitals_api.resume_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.ACTIVE
    assert response["status"] == HospitalStatus.ACTIVE
    assert db.committed is True
    assert [a.action for a in db.added if hasattr(a, "action")] == ["resume_hospital"]


async def test_resume_allows_missing_schedule():
    hospital = _full_hospital(status=HospitalStatus.PAUSED, site_live=False, schedule_set=False)
    db = _LifecycleDB(hospital)

    response = await hospitals_api.resume_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.ACTIVE
    assert response["status"] == HospitalStatus.ACTIVE
    assert db.committed is True
    assert [a.action for a in db.added if hasattr(a, "action")] == ["resume_hospital"]


async def test_resume_rejected_when_not_paused():
    hospital = _full_hospital(status=HospitalStatus.ACTIVE)
    db = _LifecycleDB(hospital)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.resume_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert db.committed is False


@pytest.mark.parametrize(
    ("missing_key", "overrides", "handoff_state"),
    [
        ("profile_complete", {"profile_complete": False}, HandoffState.HANDOFF_ACCEPTED),
        ("v0_report_done", {"v0_report_done": False}, HandoffState.HANDOFF_ACCEPTED),
        ("site_built", {"site_built": False}, HandoffState.HANDOFF_ACCEPTED),
    ],
)
async def test_resume_blocks_each_authoritative_gate_without_interval(
    missing_key, overrides, handoff_state
):
    hospital = _full_hospital(status=HospitalStatus.PAUSED, **overrides)
    db = _LifecycleDB(hospital, handoff_state=handoff_state)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.resume_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["missing"] == [missing_key]
    assert hospital.status == HospitalStatus.PAUSED
    assert not any(isinstance(item, HospitalServiceInterval) for item in db.added)


async def test_resume_custom_domain_requires_current_dns(monkeypatch):
    hospital = _full_hospital(
        status=HospitalStatus.PAUSED,
        aeo_domain="clinic.example.com",
    )
    db = _LifecycleDB(hospital)

    async def _unverified_dns(domain, strategy):
        return SimpleNamespace(verified=False)

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _unverified_dns)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.resume_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "DOMAIN_NOT_READY"
    assert hospital.status == HospitalStatus.PAUSED
    assert db.added == []


async def test_resume_custom_domain_requires_current_certificate(monkeypatch):
    hospital = _full_hospital(
        status=HospitalStatus.PAUSED,
        aeo_domain="clinic.example.com",
    )
    db = _LifecycleDB(hospital)

    async def _verified_dns(domain, strategy):
        return SimpleNamespace(verified=True)

    async def _pending_certificate(domain):
        return SimpleNamespace(
            ready=False,
            phase="PROVISIONING",
            message="HTTPS 인증서를 준비하고 있습니다.",
        )

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _verified_dns)
    monkeypatch.setattr(
        hospitals_api, "ensure_verified_domain_certificate", _pending_certificate
    )

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.resume_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "CERTIFICATE_NOT_READY"
    assert hospital.status == HospitalStatus.PAUSED
    assert db.added == []
