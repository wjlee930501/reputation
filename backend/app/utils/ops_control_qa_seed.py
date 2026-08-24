"""Deterministic local QA fixtures for operations-control scenarios."""

import argparse
import json
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.admin_user import AdminUser
from app.models.lead import SalesLead
from app.services.admin_passwords import hash_admin_password
from app.utils.ops_control_qa_cleanup import (
    CleanupCounts,
    UnsafeCleanupTarget,
    count_remaining_targets,
    delete_cleanup_targets,
    verify_cleanup_targets,
)
from app.utils.ops_control_qa_journey import SlackFixture, ensure_complete_journey

QA_PREFIX = "OPS-QA-20260810"
QA_ADMIN_EMAIL = "operator.owner.20260810@example.invalid"
QA_SOURCE_MARKER: Final = "[OPS-QA-20260810]"
QA_FIXTURE: Final = "ops-control-qa"
QA_MANIFEST_VERSION: Final = 1
QA_ADMIN_IDENTITIES: Final = {
    QA_ADMIN_EMAIL: "김민지",
    "operator.sales.20260810@example.invalid": "박서준",
    "operator.ae.20260810@example.invalid": "이수진",
}
QA_LEAD_CLINIC_NAME: Final = "장편한외과의원"
QA_LEAD_QUESTION: Final = "월간 콘텐츠 운영 상담을 신청합니다."


class CleanupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture: str
    manifest_version: int
    prefix: str
    admin_user_ids: list[uuid.UUID]
    lead_ids: list[uuid.UUID]
    hospital_ids: list[uuid.UUID]
    handoff_ids: list[uuid.UUID] = Field(default_factory=list)
    schedule_ids: list[uuid.UUID] = Field(default_factory=list)
    content_ids: list[uuid.UUID] = Field(default_factory=list)
    report_ids: list[uuid.UUID] = Field(default_factory=list)
    source_asset_ids: list[uuid.UUID] = Field(default_factory=list)
    lead_diagnosis_ids: list[uuid.UUID] = Field(default_factory=list)
    operation_run_ids: list[uuid.UUID] = Field(default_factory=list)
    incident_ids: list[uuid.UUID] = Field(default_factory=list)
    outbox_ids: list[uuid.UUID] = Field(default_factory=list)


class CleanupManifestError(RuntimeError):
    """The cleanup request does not exclusively identify owned QA fixtures."""


class SerializedManifest(TypedDict):
    fixture: str
    manifest_version: int
    prefix: str
    admin_user_ids: list[str]
    lead_ids: list[str]
    hospital_ids: list[str]
    handoff_ids: list[str]
    schedule_ids: list[str]
    content_ids: list[str]
    report_ids: list[str]
    source_asset_ids: list[str]
    lead_diagnosis_ids: list[str]
    operation_run_ids: list[str]
    incident_ids: list[str]
    outbox_ids: list[str]


class SeedResult(TypedDict):
    manifest: SerializedManifest
    credential_path: str
    slack_fixtures: list[SlackFixture]


class CleanupResult(TypedDict):
    remaining_recorded_ids: int
    credential_removed: bool
    zero_counts: CleanupCounts


def _read_manifest(path: Path) -> CleanupManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    payload = raw.get("manifest", raw) if isinstance(raw, dict) else raw
    manifest = CleanupManifest.model_validate(payload)
    if (
        manifest.fixture != QA_FIXTURE
        or manifest.manifest_version != QA_MANIFEST_VERSION
        or manifest.prefix != QA_PREFIX
    ):
        raise CleanupManifestError("manifest provenance does not match this QA fixture")
    for ids in (
        manifest.admin_user_ids,
        manifest.lead_ids,
        manifest.hospital_ids,
        manifest.handoff_ids,
        manifest.schedule_ids,
        manifest.content_ids,
        manifest.report_ids,
        manifest.source_asset_ids,
        manifest.lead_diagnosis_ids,
        manifest.operation_run_ids,
        manifest.incident_ids,
        manifest.outbox_ids,
    ):
        if len(ids) != len(set(ids)):
            raise CleanupManifestError("manifest contains duplicate record IDs")
    return manifest


def _verify_cleanup_targets(db, manifest: CleanupManifest) -> None:
    """Fetch and verify every row before the first destructive statement."""
    try:
        verify_cleanup_targets(
            db,
            manifest,
            source_marker=QA_SOURCE_MARKER,
            admin_identities=QA_ADMIN_IDENTITIES,
        )
    except UnsafeCleanupTarget as exc:
        raise CleanupManifestError(str(exc)) from exc


def _upsert_admin(db, email: str, name: str, role: str, password: str) -> AdminUser:
    account = db.execute(select(AdminUser).where(AdminUser.email == email)).scalar_one_or_none()
    if account is None:
        account = AdminUser(
            email=email,
            name=name,
            role=role,
            password_hash=hash_admin_password(password),
            is_active=True,
            is_operations_test=True,
        )
        db.add(account)
        db.flush()
    else:
        account.name = name
        account.role = role
        account.is_active = True
        account.is_operations_test = True
        account.password_hash = hash_admin_password(password)
    return account


def seed(evidence_dir: Path) -> SeedResult:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    password_path = evidence_dir / "ops-qa-admin.json"
    password = secrets.token_urlsafe(24)
    with SyncSessionLocal() as db:
        owner = _upsert_admin(db, QA_ADMIN_EMAIL, "김민지", "OWNER", password)
        sales = _upsert_admin(
            db, "operator.sales.20260810@example.invalid", "박서준", "OPERATOR", password
        )
        ae = _upsert_admin(
            db, "operator.ae.20260810@example.invalid", "이수진", "OPERATOR", password
        )
        lead = db.execute(
            select(SalesLead).where(
                SalesLead.source_path == "/ops-qa",
                SalesLead.consent_version == "ops-qa-v1",
                SalesLead.conversion_note == QA_SOURCE_MARKER,
            )
        ).scalar_one_or_none()
        if lead is None:
            lead = SalesLead(
                clinic_name=QA_LEAD_CLINIC_NAME,
                clinic_type="외과",
                contact="010-2468-1357",
                question=QA_LEAD_QUESTION,
                privacy=True,
                source_path="/ops-qa",
                consent_version="ops-qa-v1",
                conversion_note=QA_SOURCE_MARKER,
            )
            db.add(lead)
            db.flush()
        lead.clinic_name = QA_LEAD_CLINIC_NAME
        lead.clinic_type = "외과"
        lead.contact = "010-2468-1357"
        lead.question = QA_LEAD_QUESTION
        lead.conversion_note = QA_SOURCE_MARKER
        journey = ensure_complete_journey(db, lead.id, owner.id, sales.id, ae.id)
        hospital_id = journey["hospital_ids"][0]
        lead.converted_hospital_id = hospital_id
        lead.converted_at = lead.converted_at or datetime.now(UTC)
        lead.status = "CONVERTED"
        manifest: SerializedManifest = {
            "fixture": QA_FIXTURE,
            "manifest_version": QA_MANIFEST_VERSION,
            "prefix": QA_PREFIX,
            "admin_user_ids": [str(owner.id), str(sales.id), str(ae.id)],
            "lead_ids": [str(lead.id)],
            "hospital_ids": [str(value) for value in journey["hospital_ids"]],
            "handoff_ids": [str(value) for value in journey["handoff_ids"]],
            "schedule_ids": [str(value) for value in journey["schedule_ids"]],
            "content_ids": [str(value) for value in journey["content_ids"]],
            "report_ids": [str(value) for value in journey["report_ids"]],
            "source_asset_ids": [str(value) for value in journey["source_asset_ids"]],
            "lead_diagnosis_ids": [str(value) for value in journey["lead_diagnosis_ids"]],
            "operation_run_ids": [str(value) for value in journey["operation_run_ids"]],
            "incident_ids": [str(value) for value in journey["incident_ids"]],
            "outbox_ids": [str(value) for value in journey["outbox_ids"]],
        }
        db.commit()
    password_path.write_text(
        json.dumps(
            {
                "email": QA_ADMIN_EMAIL,
                "password": password,
                "manifest": manifest,
                "slack_fixtures": journey["slack_fixtures"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.chmod(password_path, 0o600)
    return {
        "manifest": manifest,
        "credential_path": str(password_path),
        "slack_fixtures": journey["slack_fixtures"],
    }


def cleanup(manifest_path: Path) -> CleanupResult:
    manifest = _read_manifest(manifest_path)
    with SyncSessionLocal() as db:
        _verify_cleanup_targets(db, manifest)
        delete_cleanup_targets(db, manifest)
        db.commit()
        zero_counts = count_remaining_targets(db, manifest)
    remaining_recorded_ids = sum(zero_counts.values())
    if remaining_recorded_ids:
        surviving = ", ".join(
            f"{group}={count}" for group, count in zero_counts.items() if count
        )
        raise CleanupManifestError(f"recorded QA rows survived cleanup: {surviving}")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    credential_removed = bool(
        isinstance(raw, dict)
        and raw.get("email") == QA_ADMIN_EMAIL
        and isinstance(raw.get("password"), str)
        and manifest_path.name == "ops-qa-admin.json"
    )
    if credential_removed:
        manifest_path.unlink()
    return {
        "remaining_recorded_ids": remaining_recorded_ids,
        "credential_removed": credential_removed,
        "zero_counts": zero_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--seed", action="store_true")
    mode.add_argument("--cleanup-manifest", type=Path)
    parser.add_argument("--evidence-dir", type=Path, default=Path(".omo/evidence"))
    parser.add_argument("--json", action="store_true", help="emit machine-readable details")
    args = parser.parse_args()
    try:
        if args.seed:
            result = seed(args.evidence_dir)
            payload = result if args.json else result["manifest"]
        else:
            payload = cleanup(args.cleanup_manifest)
        print(json.dumps(payload, ensure_ascii=False))
    except (CleanupManifestError, ValidationError, json.JSONDecodeError) as exc:
        print(f"cleanup refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
