"""Deterministic local QA fixtures for operations-control scenarios."""

import argparse
import json
import os
import secrets
import sys
import uuid
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import delete, select

from app.core.database import SyncSessionLocal
from app.models.admin_user import AdminUser
from app.models.hospital import Hospital
from app.models.lead import SalesLead
from app.services.admin_passwords import hash_admin_password

QA_PREFIX = "OPS-QA-20260810"
QA_ADMIN_EMAIL = "ops-qa-20260810@example.invalid"
QA_SOURCE_MARKER: Final = "[OPS-QA-20260810]"
QA_FIXTURE: Final = "ops-control-qa"
QA_MANIFEST_VERSION: Final = 1
QA_ADMIN_IDENTITIES: Final = {
    QA_ADMIN_EMAIL: "Ops QA Owner",
    "ops-qa-sales-20260810@example.invalid": "Sales QA",
    "ops-qa-ae-20260810@example.invalid": "AE QA",
}


class CleanupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture: str
    manifest_version: int
    prefix: str
    admin_user_ids: list[uuid.UUID]
    lead_ids: list[uuid.UUID]
    hospital_ids: list[uuid.UUID]


class CleanupManifestError(RuntimeError):
    """The cleanup request does not exclusively identify owned QA fixtures."""


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
    for ids in (manifest.admin_user_ids, manifest.lead_ids, manifest.hospital_ids):
        if len(ids) != len(set(ids)):
            raise CleanupManifestError("manifest contains duplicate record IDs")
    return manifest


def _verify_cleanup_targets(db, manifest: CleanupManifest) -> None:
    """Fetch and verify every row before the first destructive statement."""
    qa_lead_ids = set(manifest.lead_ids)
    for record_id in manifest.lead_ids:
        lead = db.get(SalesLead, record_id)
        if (
            lead is None
            or lead.clinic_name != f"{QA_PREFIX}-LEAD"
            or lead.source_path != "/ops-qa"
            or lead.consent_version != "ops-qa-v1"
        ):
            raise CleanupManifestError(f"lead {record_id} is absent or not an owned QA fixture")
    for record_id in manifest.hospital_ids:
        hospital = db.get(Hospital, record_id)
        source_owned = hospital is not None and hospital.source_lead_id in qa_lead_ids
        marker_owned = (
            hospital is not None
            and isinstance(hospital.onboarding_note, str)
            and QA_SOURCE_MARKER in hospital.onboarding_note
        )
        if (
            hospital is None
            or not hospital.name.startswith(f"{QA_PREFIX}-")
            or not (source_owned or marker_owned)
        ):
            raise CleanupManifestError(
                f"hospital {record_id} is absent or lacks immutable QA provenance"
            )
    for record_id in manifest.admin_user_ids:
        account = db.get(AdminUser, record_id)
        expected_name = QA_ADMIN_IDENTITIES.get(account.email) if account is not None else None
        if account is None or expected_name is None or account.name != expected_name:
            raise CleanupManifestError(
                f"admin account {record_id} is absent or outside the QA identity namespace"
            )


def _upsert_admin(db, email: str, name: str, role: str, password: str) -> AdminUser:
    account = db.execute(select(AdminUser).where(AdminUser.email == email)).scalar_one_or_none()
    if account is None:
        account = AdminUser(
            email=email,
            name=name,
            role=role,
            password_hash=hash_admin_password(password),
            is_active=True,
        )
        db.add(account)
        db.flush()
    else:
        account.name = name
        account.role = role
        account.is_active = True
        account.password_hash = hash_admin_password(password)
    return account


def seed(evidence_dir: Path) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    password_path = evidence_dir / "ops-qa-admin.json"
    password = secrets.token_urlsafe(24)
    with SyncSessionLocal() as db:
        owner = _upsert_admin(db, QA_ADMIN_EMAIL, "Ops QA Owner", "OWNER", password)
        sales = _upsert_admin(
            db, "ops-qa-sales-20260810@example.invalid", "Sales QA", "OPERATOR", password
        )
        ae = _upsert_admin(db, "ops-qa-ae-20260810@example.invalid", "AE QA", "OPERATOR", password)
        lead = db.execute(
            select(SalesLead).where(SalesLead.clinic_name == f"{QA_PREFIX}-LEAD")
        ).scalar_one_or_none()
        if lead is None:
            lead = SalesLead(
                clinic_name=f"{QA_PREFIX}-LEAD",
                clinic_type="서울 외과",
                contact="010-0000-0000",
                question="QA handoff",
                privacy=True,
                source_path="/ops-qa",
                consent_version="ops-qa-v1",
            )
            db.add(lead)
            db.flush()
        db.commit()
        manifest = {
            "fixture": QA_FIXTURE,
            "manifest_version": QA_MANIFEST_VERSION,
            "prefix": QA_PREFIX,
            "admin_user_ids": [str(owner.id), str(sales.id), str(ae.id)],
            "lead_ids": [str(lead.id)],
            "hospital_ids": [],
        }
    password_path.write_text(
        json.dumps({"email": QA_ADMIN_EMAIL, "password": password, "manifest": manifest}, indent=2),
        encoding="utf-8",
    )
    os.chmod(password_path, 0o600)
    print(json.dumps(manifest))
    return password_path


def cleanup(manifest_path: Path) -> None:
    manifest = _read_manifest(manifest_path)
    with SyncSessionLocal() as db:
        _verify_cleanup_targets(db, manifest)
        if manifest.hospital_ids:
            db.execute(delete(Hospital).where(Hospital.id.in_(manifest.hospital_ids)))
        if manifest.lead_ids:
            db.execute(delete(SalesLead).where(SalesLead.id.in_(manifest.lead_ids)))
        if manifest.admin_user_ids:
            db.execute(delete(AdminUser).where(AdminUser.id.in_(manifest.admin_user_ids)))
        db.commit()
    print(json.dumps({"remaining_recorded_ids": 0}))


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--seed", action="store_true")
    mode.add_argument("--cleanup-manifest", type=Path)
    parser.add_argument("--evidence-dir", type=Path, default=Path(".omo/evidence"))
    args = parser.parse_args()
    try:
        if args.seed:
            seed(args.evidence_dir)
        else:
            cleanup(args.cleanup_manifest)
    except (CleanupManifestError, ValidationError, json.JSONDecodeError) as exc:
        print(f"cleanup refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
