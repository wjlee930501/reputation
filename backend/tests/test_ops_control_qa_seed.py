import json
import sys
import uuid
from typing import TypedDict

import pytest

from app.models.admin_user import AdminUser
from app.utils import ops_control_qa_seed
from app.utils.ops_control_qa_records import (
    QA_CONTENT_TITLE,
    QA_HOSPITAL_NAME,
    QA_SOURCE_TITLE,
)
from app.utils.ops_control_qa_scenarios import scenario_index


def test_manifest_has_deterministic_fixture_identity() -> None:
    assert ops_control_qa_seed.QA_ADMIN_EMAIL == "operator.owner.20260810@example.invalid"
    assert ops_control_qa_seed.QA_PREFIX == "OPS-QA-20260810"


def test_visible_fixture_copy_looks_like_real_operator_data() -> None:
    visible_values = [
        *ops_control_qa_seed.QA_ADMIN_IDENTITIES.values(),
        ops_control_qa_seed.QA_LEAD_CLINIC_NAME,
        ops_control_qa_seed.QA_LEAD_QUESTION,
        QA_HOSPITAL_NAME,
        QA_CONTENT_TITLE,
        QA_SOURCE_TITLE,
    ]

    for value in visible_values:
        assert "OPS-QA" not in value
        assert " QA" not in value
        assert not value.startswith("QA ")


def test_manifest_covers_the_complete_marketer_journey() -> None:
    required = {
        "handoff_ids",
        "content_ids",
        "report_ids",
        "lead_diagnosis_ids",
        "operation_run_ids",
        "incident_ids",
        "outbox_ids",
    }

    assert required <= set(ops_control_qa_seed.CleanupManifest.model_fields)


def test_legacy_manifest_remains_cleanup_compatible() -> None:
    manifest = ops_control_qa_seed.CleanupManifest.model_validate(_manifest([]))

    assert manifest.handoff_ids == []
    assert manifest.content_ids == []
    assert manifest.report_ids == []
    assert manifest.lead_diagnosis_ids == []
    assert manifest.operation_run_ids == []
    assert manifest.incident_ids == []
    assert manifest.outbox_ids == []


class FakeSession:
    def __init__(self, records, *, retain_after_commit: bool = False):
        self.records = records
        self.retain_after_commit = retain_after_commit
        self.deleted = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, model, record_id):
        if self.committed and not self.retain_after_commit:
            return None
        return self.records.get((model, record_id))

    def execute(self, statement):
        self.deleted.append(statement)

    def commit(self):
        self.committed = True


def _admin(record_id: uuid.UUID, *, qa: bool) -> AdminUser:
    return AdminUser(
        id=record_id,
        email=(ops_control_qa_seed.QA_ADMIN_EMAIL if qa else "owner@hospital.example"),
        name=("김민지" if qa else "Production Owner"),
        role="OWNER",
        password_hash="not-used",
        is_active=True,
    )


class FixtureManifest(TypedDict):
    fixture: str
    manifest_version: int
    prefix: str
    admin_user_ids: list[str]
    lead_ids: list[str]
    hospital_ids: list[str]


def _manifest(admin_ids: list[uuid.UUID]) -> FixtureManifest:
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

    result = ops_control_qa_seed.cleanup(path)

    assert len(session.deleted) == 1
    assert session.committed is True
    assert result["remaining_recorded_ids"] == 0
    assert result["credential_removed"] is False


def test_cleanup_refuses_a_false_zero_receipt_when_a_record_survives_commit(
    tmp_path, monkeypatch
) -> None:
    qa_id = uuid.uuid4()
    session = FakeSession(
        {(AdminUser, qa_id): _admin(qa_id, qa=True)},
        retain_after_commit=True,
    )
    path = tmp_path / "ops-qa-admin.json"
    path.write_text(
        json.dumps(
            {
                "email": ops_control_qa_seed.QA_ADMIN_EMAIL,
                "password": "temporary-test-password",
                "manifest": _manifest([qa_id]),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ops_control_qa_seed, "SyncSessionLocal", lambda: session)

    with pytest.raises(ops_control_qa_seed.CleanupManifestError, match="survived cleanup"):
        ops_control_qa_seed.cleanup(path)

    assert path.exists(), "credential evidence must remain until every recorded row is gone"


def test_json_mode_never_prints_the_password(monkeypatch, capsys) -> None:
    result = {
        "manifest": _manifest([]),
        "credential_path": "/tmp/ops-qa-admin.json",
        "slack_fixtures": [{"text": "안전한 운영 알림", "blocks": []}],
    }
    monkeypatch.setattr(ops_control_qa_seed, "seed", lambda _path: result)
    monkeypatch.setattr(sys, "argv", ["ops_control_qa_seed", "--seed", "--json"])

    assert ops_control_qa_seed.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == result
    assert "password" not in json.dumps(payload)


def test_scenario_index_builds_complete_unique_records_without_repository_plan() -> None:
    plan = """
- [ ] 1. 첫 운영 시나리오
    Scenario: 인수 승인
    Tool: browser
    Steps: 승인 버튼을 누른다
    Expected: ACCEPTED
    Evidence: task-01.json
- [ ] 26. 마지막 운영 시나리오
    Scenario: 예외 복구
    Tool: curl
    Steps: 복구 요청을 보낸다
    Expected: 200
    Evidence: task-26.json
"""

    scenarios = scenario_index(plan)

    assert [row["id"] for row in scenarios] == ["task-01-scenario-01", "task-26-scenario-01"]
    assert all(row["command_or_channel"] for row in scenarios)
    assert all(row["expected_http_or_state"] for row in scenarios)
    assert all(row["evidence_path"] for row in scenarios)
    assert [row["cleanup_receipt"] for row in scenarios] == [
        "task-01-cleanup.json",
        "task-26-cleanup.json",
    ]
