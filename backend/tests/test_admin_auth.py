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


# ── CDX-M3: Redis-backed login throttle (shared across instances) ───────────


class FakeStrategy:
    """limits 전략 인터페이스(test/hit/clear) 흉내 — 실패 카운트만 추적."""

    def __init__(self, limit_amount: int = 5):
        self.limit_amount = limit_amount
        self.hits: dict[str, int] = {}
        self.cleared: list[str] = []

    def test(self, _item, key: str) -> bool:
        return self.hits.get(key, 0) < self.limit_amount

    def hit(self, _item, key: str) -> bool:
        self.hits[key] = self.hits.get(key, 0) + 1
        return self.hits[key] <= self.limit_amount

    def clear(self, _item, key: str) -> None:
        self.cleared.append(key)
        self.hits.pop(key, None)


def build_throttled_request(strategy: FakeStrategy, client_ip: str = "203.0.113.9"):
    from types import SimpleNamespace

    limiter = SimpleNamespace(enabled=True, limiter=strategy)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(limiter=limiter)),
        client=SimpleNamespace(host=client_ip),
        headers=SimpleNamespace(get=lambda *_args, **_kw: None),
    )


@pytest.mark.asyncio
async def test_login_failures_hit_email_and_ip_keys_then_429():
    strategy = FakeStrategy(limit_amount=5)
    db = FakeDB(build_admin_user())
    request = build_throttled_request(strategy)
    body = AdminLoginRequest(email="owner@example.com", password="wrong-password")

    for _ in range(5):
        with pytest.raises(HTTPException) as exc:
            await login_admin(body, db, request=request)
        assert exc.value.status_code == 401

    assert strategy.hits["admin-login:email:owner@example.com"] == 5
    assert strategy.hits["admin-login:ip:203.0.113.9"] == 5

    with pytest.raises(HTTPException) as exc:
        await login_admin(body, db, request=request)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_login_success_clears_throttle_keys():
    strategy = FakeStrategy()
    db = FakeDB(build_admin_user())
    request = build_throttled_request(strategy)

    with pytest.raises(HTTPException):
        await login_admin(
            AdminLoginRequest(email="owner@example.com", password="wrong-password"),
            db,
            request=request,
        )
    assert strategy.hits["admin-login:email:owner@example.com"] == 1

    response = await login_admin(
        AdminLoginRequest(email="owner@example.com", password="correct horse battery staple"),
        db,
        request=request,
    )
    assert response.email == "owner@example.com"
    assert "admin-login:email:owner@example.com" in strategy.cleared
    assert "admin-login:email:owner@example.com" not in strategy.hits


@pytest.mark.asyncio
async def test_login_throttle_skipped_without_limiter():
    # request 없이 직접 호출(기존 테스트 경로)도, limiter 미장착 앱도 로그인은 동작해야 한다.
    db = FakeDB(build_admin_user())
    response = await login_admin(
        AdminLoginRequest(email="owner@example.com", password="correct horse battery staple"),
        db,
    )
    assert response.email == "owner@example.com"
