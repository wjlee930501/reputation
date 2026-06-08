"""Lead PII anonymization + single-lead erasure (PII-2 / PII-3)."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import leads as leads_api
from app.services.lead_privacy import anonymize_lead


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
