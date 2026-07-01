from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.api.admin.auth import (
    AdminSessionRevocationRequest,
    get_admin_session_revocation,
    revoke_admin_session,
)
from app.services.admin_session_revocation import (
    AdminSessionRevocationUnavailable,
    is_admin_session_hash_revoked,
    revoke_admin_session_hash,
)


TOKEN_HASH = "a" * 64


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int):
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def exists(self, key: str):
        return int(key in self.values)


@pytest.mark.asyncio
async def test_revoked_admin_session_hash_is_reported_revoked():
    redis_client = FakeRedis()
    now = datetime(2026, 7, 1, tzinfo=UTC)

    ttl = await revoke_admin_session_hash(
        TOKEN_HASH,
        expires_at=now + timedelta(minutes=10),
        redis_client=redis_client,
        now=now,
    )

    assert ttl == 600
    assert redis_client.ttls[f"admin-session-revoked:{TOKEN_HASH}"] == 600
    assert await is_admin_session_hash_revoked(TOKEN_HASH, redis_client=redis_client) is True


@pytest.mark.asyncio
async def test_unknown_admin_session_hash_is_active():
    assert await is_admin_session_hash_revoked(TOKEN_HASH, redis_client=FakeRedis()) is False


@pytest.mark.asyncio
async def test_expired_admin_session_hash_revocation_gets_zero_ttl_and_no_write():
    redis_client = FakeRedis()
    now = datetime(2026, 7, 1, tzinfo=UTC)

    ttl = await revoke_admin_session_hash(
        TOKEN_HASH,
        expires_at=now - timedelta(seconds=1),
        redis_client=redis_client,
        now=now,
    )

    assert ttl == 0
    assert redis_client.values == {}


@pytest.mark.asyncio
async def test_admin_session_revocation_check_fails_closed_when_redis_unavailable(monkeypatch):
    async def fail_check(_token_hash: str):
        raise AdminSessionRevocationUnavailable("redis unavailable")

    monkeypatch.setattr("app.api.admin.auth.is_admin_session_hash_revoked", fail_check)

    with pytest.raises(HTTPException) as exc:
        await get_admin_session_revocation(TOKEN_HASH)

    assert exc.value.status_code == 503
    assert exc.value.detail == "Admin session revocation state unavailable"


@pytest.mark.asyncio
async def test_revoke_admin_session_route_returns_revoked(monkeypatch):
    captured: dict[str, str | datetime] = {}
    expires_at = datetime(2026, 7, 1, tzinfo=UTC) + timedelta(minutes=10)

    async def fake_revoke(token_hash: str, *, expires_at: datetime) -> int:
        captured["token_hash"] = token_hash
        captured["expires_at"] = expires_at
        return 600

    monkeypatch.setattr("app.api.admin.auth.revoke_admin_session_hash", fake_revoke)

    response = await revoke_admin_session(
        AdminSessionRevocationRequest(token_hash=TOKEN_HASH, expires_at=expires_at)
    )

    assert response.revoked is True
    assert captured == {"token_hash": TOKEN_HASH, "expires_at": expires_at}


@pytest.mark.asyncio
async def test_revoke_admin_session_route_translates_invalid_hash_to_400(monkeypatch):
    async def fake_revoke(_token_hash: str, *, expires_at: datetime):
        raise ValueError("bad hash")

    monkeypatch.setattr("app.api.admin.auth.revoke_admin_session_hash", fake_revoke)

    with pytest.raises(HTTPException) as exc:
        await revoke_admin_session(
            AdminSessionRevocationRequest(token_hash=TOKEN_HASH, expires_at=datetime.now(UTC))
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid admin session token hash"


@pytest.mark.asyncio
async def test_revoke_admin_session_route_translates_redis_unavailable_to_503(monkeypatch):
    async def fake_revoke(_token_hash: str, *, expires_at: datetime):
        raise AdminSessionRevocationUnavailable("redis unavailable")

    monkeypatch.setattr("app.api.admin.auth.revoke_admin_session_hash", fake_revoke)

    with pytest.raises(HTTPException) as exc:
        await revoke_admin_session(
            AdminSessionRevocationRequest(token_hash=TOKEN_HASH, expires_at=datetime.now(UTC))
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "Admin session revocation state unavailable"
