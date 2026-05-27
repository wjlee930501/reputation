import uuid

import pytest
from fastapi import HTTPException

from app.api.admin.auth import AdminLoginRequest, login_admin
from app.models.admin_user import AdminUser
from app.services.admin_passwords import hash_admin_password


class FakeResult:
    def __init__(self, user: AdminUser | None):
        self.user = user

    def scalar_one_or_none(self):
        return self.user


class FakeDB:
    def __init__(self, user: AdminUser | None):
        self.user = user
        self.committed = False

    async def execute(self, _stmt):
        return FakeResult(self.user)

    async def commit(self):
        self.committed = True


def build_admin_user(*, password: str = "correct horse battery staple", is_active: bool = True):
    return AdminUser(
        id=uuid.uuid4(),
        email="owner@example.com",
        name="Owner",
        role="OWNER",
        password_hash=hash_admin_password(password),
        is_active=is_active,
    )


@pytest.mark.asyncio
async def test_login_admin_returns_account_and_updates_last_login():
    user = build_admin_user()
    db = FakeDB(user)

    response = await login_admin(
        AdminLoginRequest(email=" Owner@Example.COM ", password="correct horse battery staple"),
        db,
    )

    assert response.email == "owner@example.com"
    assert response.name == "Owner"
    assert response.role == "OWNER"
    assert user.last_login_at is not None
    assert db.committed


@pytest.mark.asyncio
async def test_login_admin_rejects_bad_password_without_commit():
    db = FakeDB(build_admin_user())

    with pytest.raises(HTTPException) as exc:
        await login_admin(AdminLoginRequest(email="owner@example.com", password="wrong-password"), db)

    assert exc.value.status_code == 401
    assert not db.committed


@pytest.mark.asyncio
async def test_login_admin_rejects_inactive_accounts():
    db = FakeDB(build_admin_user(is_active=False))

    with pytest.raises(HTTPException) as exc:
        await login_admin(
            AdminLoginRequest(email="owner@example.com", password="correct horse battery staple"),
            db,
        )

    assert exc.value.status_code == 401
    assert not db.committed
