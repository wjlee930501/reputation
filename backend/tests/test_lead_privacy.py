"""Lead PII anonymization + single-lead erasure (PII-2 / PII-3 / CDX-M2)."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import leads as leads_api
from app.services.lead_privacy import anonymize_lead, scrub_onboarding_note


def _lead(**overrides):
    base = dict(
        id=uuid.uuid4(),
        clinic_name="강남 치과",
        clinic_type="강남 치과",
        contact="010-1111-2222",
        question="임플란트 상담 문의",
        consent_ip="203.0.113.7",
        conversion_note="연락처: 010-1111-2222",
        source_path="/",
        consent_version="v1.test",
        converted_hospital_id=None,
        purged_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_anonymize_lead_clears_pii_keeps_stats():
    lead = _lead()
    now = datetime.now(timezone.utc)

    changed = anonymize_lead(lead, now)

    assert changed is True
    assert lead.contact == "[purged]"
    assert lead.question == "[purged]"
    assert lead.clinic_name == "[purged]"
    assert lead.consent_ip is None
    assert lead.conversion_note == "[purged]"
    assert lead.purged_at == now
    # statistical metadata retained
    assert lead.clinic_type == "강남 치과"
    assert lead.source_path == "/"
    assert lead.consent_version == "v1.test"


def test_anonymize_lead_is_idempotent():
    already = _lead(purged_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert anonymize_lead(already, datetime.now(timezone.utc)) is False


class _FakeDB:
    def __init__(self, lead):
        self._lead = lead
        self.added = []
        self.committed = False

    async def get(self, model, object_id):
        return self._lead if self._lead and self._lead.id == object_id else None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True


async def test_erase_lead_pii_anonymizes_and_audits():
    lead = _lead()
    db = _FakeDB(lead)

    result = await leads_api.erase_lead_pii(lead.id, db=db)

    assert result["detail"] == "erased"
    assert lead.contact == "[purged]"
    assert lead.purged_at is not None
    assert db.committed is True
    # an audit row was written for the erasure
    assert any(getattr(a, "action", None) == "erase_lead_pii" for a in db.added)


async def test_erase_lead_pii_idempotent_when_already_purged():
    lead = _lead(purged_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    db = _FakeDB(lead)

    result = await leads_api.erase_lead_pii(lead.id, db=db)

    assert result["detail"] == "already_purged"
    assert db.committed is False  # nothing to commit


async def test_erase_lead_pii_404_when_missing():
    db = _FakeDB(None)
    with pytest.raises(HTTPException) as exc:
        await leads_api.erase_lead_pii(uuid.uuid4(), db=db)
    assert exc.value.status_code == 404


# ── CDX-M2: onboarding_note operator-note scrub ─────────────────────────────


def _note_block(lead_id, operator_note="원장 직통 010-9999-8888 로 연락"):
    return (
        f"Source lead: {lead_id}\n"
        "Clinic type / region: 강남 치과\n"
        "Source path: /\n"
        "Consent version: v1.test\n"
        f"Operator note: {operator_note}"
    )


def test_scrub_onboarding_note_purges_operator_note_keeps_meta():
    lead_id = uuid.uuid4()
    scrubbed = scrub_onboarding_note(_note_block(lead_id), lead_id)

    assert "010-9999-8888" not in scrubbed
    assert "Operator note: [purged]" in scrubbed
    # 구조화된 메타는 유지
    assert f"Source lead: {lead_id}" in scrubbed
    assert "Clinic type / region: 강남 치과" in scrubbed
    assert "Consent version: v1.test" in scrubbed


def test_scrub_onboarding_note_only_touches_matching_lead_block():
    target = uuid.uuid4()
    other = uuid.uuid4()
    note = f"{_note_block(other, '다른 리드 노트')}\n\n{_note_block(target)}"

    scrubbed = scrub_onboarding_note(note, target)

    assert "다른 리드 노트" in scrubbed  # 다른 리드 블록은 그대로
    assert "010-9999-8888" not in scrubbed


def test_scrub_onboarding_note_noop_without_block_or_note():
    lead_id = uuid.uuid4()
    assert scrub_onboarding_note(None, lead_id) is None
    assert scrub_onboarding_note("", lead_id) == ""
    assert scrub_onboarding_note("AE 메모만 있음", lead_id) == "AE 메모만 있음"
    # 블록은 있으나 operator note가 없으면 그대로
    no_op_note = f"Source lead: {lead_id}\nClinic type / region: 강남 치과"
    assert scrub_onboarding_note(no_op_note, lead_id) == no_op_note


async def test_erase_lead_pii_scrubs_converted_hospital_note():
    lead = _lead()
    hospital_id = uuid.uuid4()
    lead.converted_hospital_id = hospital_id
    hospital = SimpleNamespace(id=hospital_id, onboarding_note=_note_block(lead.id))

    class _DB(_FakeDB):
        async def get(self, model, object_id):
            if object_id == hospital_id:
                return hospital
            return await super().get(model, object_id)

    db = _DB(lead)
    result = await leads_api.erase_lead_pii(lead.id, db=db)

    assert result["detail"] == "erased"
    assert "010-9999-8888" not in hospital.onboarding_note
    assert "Operator note: [purged]" in hospital.onboarding_note


async def test_erase_lead_pii_scrubs_note_even_when_lead_already_purged():
    """R6 — 보관기간 cron이 (노트 scrub 도입 전에) lead만 파기한 경우에도, 명시적 파기
    요청은 전환된 병원의 onboarding_note 운영자 텍스트를 반드시 파기해야 한다."""
    hospital_id = uuid.uuid4()
    lead = _lead(
        purged_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        converted_hospital_id=hospital_id,
    )
    hospital = SimpleNamespace(id=hospital_id, onboarding_note=_note_block(lead.id))

    class _DB(_FakeDB):
        async def get(self, model, object_id):
            if object_id == hospital_id:
                return hospital
            return await super().get(model, object_id)

    db = _DB(lead)
    result = await leads_api.erase_lead_pii(lead.id, db=db)

    assert result["detail"] == "erased"
    assert "010-9999-8888" not in hospital.onboarding_note
    assert "Operator note: [purged]" in hospital.onboarding_note
    assert db.committed is True
    assert any(getattr(a, "action", None) == "erase_lead_pii" for a in db.added)


async def test_erase_lead_pii_already_purged_and_note_clean_is_noop():
    """lead도 파기됐고 노트도 이미 scrub됐으면 커밋 없이 already_purged."""
    hospital_id = uuid.uuid4()
    lead = _lead(
        purged_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        converted_hospital_id=hospital_id,
    )
    hospital = SimpleNamespace(
        id=hospital_id,
        onboarding_note=(
            f"Source lead: {lead.id}\nClinic type / region: 강남 치과\nOperator note: [purged]"
        ),
    )

    class _DB(_FakeDB):
        async def get(self, model, object_id):
            if object_id == hospital_id:
                return hospital
            return await super().get(model, object_id)

    db = _DB(lead)
    result = await leads_api.erase_lead_pii(lead.id, db=db)

    assert result["detail"] == "already_purged"
    assert db.committed is False
