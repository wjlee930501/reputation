"""#6/#9/#11 — create_hospital 감사 로그 + 경합 409, pause/resume 라이프사이클."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.admin import hospitals as hospitals_api
from app.models.hospital import HospitalStatus, Plan


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


# ── pause / resume ───────────────────────────────────────────────
class _LifecycleDB:
    def __init__(self, hospital):
        self.hospital = hospital
        self.added = []
        self.committed = False

    async def get(self, model, object_id):
        return self.hospital if self.hospital.id == object_id else None

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
        plan=Plan.PLAN_8,
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
    assert [a.action for a in db.added] == ["resume_hospital"]


async def test_resume_to_pending_domain_when_not_yet_live():
    hospital = _full_hospital(status=HospitalStatus.PAUSED, site_live=False, schedule_set=False)
    db = _LifecycleDB(hospital)

    response = await hospitals_api.resume_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    assert response["status"] == HospitalStatus.PENDING_DOMAIN
    audit = db.added[0]
    assert audit.action == "resume_hospital"
    # Scheduling is STEP 6 operational readiness, not the STEP 5 public-live gate.
    assert audit.detail["activation_missing"] == []


async def test_resume_rejected_when_not_paused():
    hospital = _full_hospital(status=HospitalStatus.ACTIVE)
    db = _LifecycleDB(hospital)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.resume_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert db.committed is False
