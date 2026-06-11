import uuid
from types import SimpleNamespace

from app.api.admin import leads as leads_api
from app.models.hospital import Hospital, Plan
from app.models.lead import SalesLead


class EmptyScalars:
    def all(self):
        return []


class EmptyResult:
    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return EmptyScalars()


class FakeDB:
    def __init__(self, lead=None, hospital=None):
        self.lead = lead
        self.hospital = hospital
        self.added = []
        self.committed = False
        self.flushed = False

    async def get(self, model, object_id):
        if model is SalesLead and self.lead and self.lead.id == object_id:
            return self.lead
        if model is Hospital and self.hospital and self.hospital.id == object_id:
            return self.hospital
        return None

    async def execute(self, _stmt):
        return EmptyResult()

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.flushed = True
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()

    async def commit(self):
        self.committed = True

    async def refresh(self, _item):
        return None


def _lead(**overrides):
    base = dict(
        id=uuid.uuid4(),
        clinic_name="온보딩치과",
        clinic_type="강남 치과",
        contact="010-1111-2222",
        question="임플란트 상담 문의",
        privacy=True,
        source_path="/",
        status="NEW",
        converted_hospital_id=None,
        converted_at=None,
        conversion_note=None,
        notification_status=None,
        notification_error=None,
        consent_version="v1.test",
        created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_onboarding_note_excludes_raw_pii():
    # PII-3: 연락처/문의 원문은 onboarding_note에 영구 저장하지 않는다(보유기간 우회 방지).
    lead = _lead()

    note = leads_api._build_onboarding_note(lead, "call before noon")

    assert f"Source lead: {lead.id}" in note
    assert "Clinic type / region: 강남 치과" in note
    assert "Operator note: call before noon" in note
    # 원문 PII는 포함되지 않아야 한다.
    assert "010-1111-2222" not in note
    assert "임플란트 상담 문의" not in note


async def test_convert_sales_lead_creates_draft_hospital_from_lead():
    lead = _lead()
    db = FakeDB(lead=lead)
    body = leads_api.LeadConvertRequest(plan=Plan.PLAN_8, conversion_note="priority onboarding")

    response = await leads_api.convert_sales_lead(lead.id, body=body, db=db)

    hospital = db.added[0]
    assert db.committed is True
    assert hospital.name == lead.clinic_name
    # PII-2: lead.contact must NOT be copied into the public hospital.phone.
    assert getattr(hospital, "phone", None) is None
    assert hospital.source_lead_id == lead.id
    assert "priority onboarding" in hospital.onboarding_note
    assert hospital.specialties == [lead.clinic_type]
    assert lead.status == "CONVERTED"
    assert lead.converted_hospital_id == hospital.id
    assert response["onboarding_url"] == f"/hospitals/{hospital.id}/onboarding"


class _CaptureDB(FakeDB):
    def __init__(self):
        super().__init__()
        self.stmt = None

    async def execute(self, stmt):
        self.stmt = stmt
        return EmptyResult()


async def test_list_sales_leads_applies_offset_and_limit():
    db = _CaptureDB()
    await leads_api.list_sales_leads(db=db, limit=50, offset=100)
    compiled = str(db.stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT 50" in compiled
    assert "OFFSET 100" in compiled
