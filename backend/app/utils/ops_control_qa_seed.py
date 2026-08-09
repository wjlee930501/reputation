"""Deterministic local QA fixtures for operations-control scenarios."""

import argparse
import json
import os
import secrets
import uuid
from pathlib import Path

from sqlalchemy import delete, select

from app.core.database import SyncSessionLocal
from app.models.admin_user import AdminUser
from app.models.hospital import Hospital
from app.models.lead import SalesLead
from app.services.admin_passwords import hash_admin_password

QA_PREFIX = "OPS-QA-20260810"
QA_ADMIN_EMAIL = "ops-qa-20260810@example.invalid"


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
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = raw.get("manifest", raw)
    hospital_ids = [uuid.UUID(value) for value in manifest.get("hospital_ids", [])]
    lead_ids = [uuid.UUID(value) for value in manifest.get("lead_ids", [])]
    admin_ids = [uuid.UUID(value) for value in manifest.get("admin_user_ids", [])]
    with SyncSessionLocal() as db:
        if hospital_ids:
            db.execute(delete(Hospital).where(Hospital.id.in_(hospital_ids)))
        if lead_ids:
            db.execute(delete(SalesLead).where(SalesLead.id.in_(lead_ids)))
        if admin_ids:
            db.execute(delete(AdminUser).where(AdminUser.id.in_(admin_ids)))
        db.commit()
        remaining = sum(
            db.execute(select(model.id).where(model.id.in_(ids))).scalars().all().__len__()
            for model, ids in (
                (Hospital, hospital_ids),
                (SalesLead, lead_ids),
                (AdminUser, admin_ids),
            )
            if ids
        )
    print(json.dumps({"remaining_recorded_ids": remaining}))


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--seed", action="store_true")
    mode.add_argument("--cleanup-manifest", type=Path)
    parser.add_argument("--evidence-dir", type=Path, default=Path(".omo/evidence"))
    args = parser.parse_args()
    if args.seed:
        seed(args.evidence_dir)
    else:
        cleanup(args.cleanup_manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
