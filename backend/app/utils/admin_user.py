"""Create or rotate Admin console accounts.

Usage:
  ADMIN_EMAIL=owner@example.com ADMIN_PASSWORD='...' python -m app.utils.admin_user create-owner
"""
import os
import sys

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.admin_user import AdminUser
from app.services.admin_passwords import hash_admin_password


def create_owner() -> int:
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "")
    name = os.getenv("ADMIN_NAME", "Owner").strip() or "Owner"
    if not email or "@" not in email:
        print("ADMIN_EMAIL must be a valid email.", file=sys.stderr)
        return 2
    if not password:
        print("ADMIN_PASSWORD is required.", file=sys.stderr)
        return 2

    password_hash = hash_admin_password(password)
    with SyncSessionLocal() as db:
        existing = db.execute(select(AdminUser).where(AdminUser.email == email)).scalar_one_or_none()
        if existing:
            existing.name = name
            existing.role = "OWNER"
            existing.password_hash = password_hash
            existing.is_active = True
            action = "updated"
        else:
            db.add(AdminUser(email=email, name=name, role="OWNER", password_hash=password_hash))
            action = "created"
        db.commit()
    print(f"Admin OWNER account {action}: {email}")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "create-owner":
        return create_owner()
    print("Usage: python -m app.utils.admin_user create-owner", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
