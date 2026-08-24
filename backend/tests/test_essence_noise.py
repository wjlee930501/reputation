"""Wave R3 Wiki evidence-noise mutation contract."""

import uuid
from types import SimpleNamespace

import pytest

from app.api.admin.essence import BulkEvidenceNoiseRequest, mark_evidence_notes_as_noise
from app.models.audit import AdminAuditLog


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _NoiseDB:
    def __init__(self, hospital_id, notes):
        self.hospital_id = hospital_id
        self.notes = notes
        self.added = []
        self.commits = 0

    async def get(self, _model, item_id):
        return SimpleNamespace(id=item_id) if item_id == self.hospital_id else None

    async def execute(self, _statement):
        return _ScalarRows(self.notes)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_bulk_mark_noise_preserves_notes_and_records_actor_audit() -> None:
    hospital_id = uuid.uuid4()
    notes = [
        SimpleNamespace(id=uuid.uuid4(), note_metadata={"extractor": "v1"}),
        SimpleNamespace(id=uuid.uuid4(), note_metadata=None),
    ]
    db = _NoiseDB(hospital_id, notes)

    response = await mark_evidence_notes_as_noise(
        hospital_id,
        BulkEvidenceNoiseRequest(note_ids=[note.id for note in notes], is_noise=True),
        db=db,
    )

    assert response.updated == 2
    assert set(response.note_ids) == {str(note.id) for note in notes}
    assert all(note.note_metadata["is_noise"] is True for note in notes)
    assert all(note.note_metadata["noise_marked_by"] for note in notes)
    assert all(note.note_metadata["noise_marked_at"] for note in notes)
    assert notes[0].note_metadata["extractor"] == "v1"
    audit = next(item for item in db.added if isinstance(item, AdminAuditLog))
    assert audit.action == "bulk_mark_evidence_noise"
    assert audit.detail == {
        "note_ids": [str(note.id) for note in notes],
        "is_noise": True,
    }
    assert db.commits == 1


@pytest.mark.asyncio
async def test_bulk_mark_noise_rejects_cross_hospital_or_missing_note() -> None:
    from fastapi import HTTPException

    hospital_id = uuid.uuid4()
    found = SimpleNamespace(id=uuid.uuid4(), note_metadata={})
    missing_id = uuid.uuid4()
    db = _NoiseDB(hospital_id, [found])

    with pytest.raises(HTTPException) as missing:
        await mark_evidence_notes_as_noise(
            hospital_id,
            BulkEvidenceNoiseRequest(note_ids=[found.id, missing_id]),
            db=db,
        )

    assert missing.value.status_code == 404
    assert db.commits == 0
    assert db.added == []
