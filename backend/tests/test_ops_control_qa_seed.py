import json
import uuid

import pytest

from app.models.admin_user import AdminUser
from app.utils import ops_control_qa_seed


def test_manifest_has_deterministic_fixture_identity() -> None:
    assert ops_control_qa_seed.QA_ADMIN_EMAIL == "ops-qa-20260810@example.invalid"
    assert ops_control_qa_seed.QA_PREFIX == "OPS-QA-20260810"


class FakeSession:
    def __init__(self, records):
        self.records = records
        self.deleted = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, model, record_id):
        return self.records.get((model, record_id))

    def execute(self, statement):
        self.deleted.append(statement)

    def commit(self):
        self.committed = True


def _admin(record_id: uuid.UUID, *, qa: bool) -> AdminUser:
    return AdminUser(
        id=record_id,
        email=("ops-qa-20260810@example.invalid" if qa else "owner@hospital.example"),
        name=("Ops QA Owner" if qa else "Production Owner"),
        role="OWNER",
        password_hash="not-used",
        is_active=True,
    )


def _manifest(admin_ids: list[uuid.UUID]) -> dict:
    return {
        "fixture": "ops-control-qa",
        "manifest_version": 1,
        "prefix": ops_control_qa_seed.QA_PREFIX,
        "admin_user_ids": [str(value) for value in admin_ids],
        "lead_ids": [],
        "hospital_ids": [],
    }


def test_cleanup_refuses_mixed_qa_and_foreign_ids_without_deleting(tmp_path, monkeypatch) -> None:
    qa_id = uuid.uuid4()
    foreign_id = uuid.uuid4()
    session = FakeSession(
        {
            (AdminUser, qa_id): _admin(qa_id, qa=True),
            (AdminUser, foreign_id): _admin(foreign_id, qa=False),
        }
    )
    path = tmp_path / "malicious.json"
    path.write_text(json.dumps(_manifest([qa_id, foreign_id])), encoding="utf-8")
    monkeypatch.setattr(ops_control_qa_seed, "SyncSessionLocal", lambda: session)

    with pytest.raises(ops_control_qa_seed.CleanupManifestError):
        ops_control_qa_seed.cleanup(path)

    assert session.deleted == []
    assert session.committed is False


def test_cleanup_refuses_missing_record_without_deleting(tmp_path, monkeypatch) -> None:
    missing_id = uuid.uuid4()
    session = FakeSession({})
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(_manifest([missing_id])), encoding="utf-8")
    monkeypatch.setattr(ops_control_qa_seed, "SyncSessionLocal", lambda: session)

    with pytest.raises(ops_control_qa_seed.CleanupManifestError):
        ops_control_qa_seed.cleanup(path)

    assert session.deleted == []
    assert session.committed is False


def test_cleanup_accepts_exact_qa_identity_manifest(tmp_path, monkeypatch) -> None:
    qa_id = uuid.uuid4()
    session = FakeSession({(AdminUser, qa_id): _admin(qa_id, qa=True)})
    path = tmp_path / "genuine.json"
    path.write_text(json.dumps(_manifest([qa_id])), encoding="utf-8")
    monkeypatch.setattr(ops_control_qa_seed, "SyncSessionLocal", lambda: session)

    ops_control_qa_seed.cleanup(path)

    assert len(session.deleted) == 1
    assert session.committed is True
