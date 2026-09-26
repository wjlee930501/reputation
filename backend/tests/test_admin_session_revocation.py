import asyncio
import os
import socket
import struct
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException

from app.api.admin.auth import (
    AdminSessionRevocationRequest,
    get_admin_session_revocation,
    revoke_admin_session,
)
from app.services import admin_session_revocation as revocation_service
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


class _ResetProxy:
    """Redis 앞의 TCP 프록시. arm() 뒤 기존 커넥션은 다음 요청에 RST로 답한다.

    유휴 중 네트워크가 상태를 버린 풀 커넥션이 다음 명령에서 곧바로 reset되는 상황이다.
    """

    def __init__(self, host: str, port: int):
        self.target = (host, port)
        self.connections: list = []
        self.upstreams: list = []
        self.armed: set = set()

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, client_reader, client_writer):
        server_reader, server_writer = await asyncio.open_connection(*self.target)
        self.connections.append(client_writer)
        self.upstreams.append(server_writer)

        async def client_to_server():
            while data := await client_reader.read(65536):
                if client_writer in self.armed:
                    client_writer.get_extra_info("socket").setsockopt(
                        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                    )
                    client_writer.transport.abort()
                    server_writer.close()
                    return
                server_writer.write(data)
                await server_writer.drain()

        async def server_to_client():
            while data := await server_reader.read(65536):
                client_writer.write(data)
                await client_writer.drain()

        await asyncio.gather(client_to_server(), server_to_client(), return_exceptions=True)

    def arm(self) -> None:
        self.armed = set(self.connections)

    def stop_accepting(self) -> None:
        self.server.close()

    def close(self) -> None:
        self.stop_accepting()
        for writer in (*self.connections, *self.upstreams):
            writer.transport.abort()


@pytest.fixture
async def reset_proxy(monkeypatch):
    raw = os.getenv("INTEGRATION_REDIS_URL")
    if not raw:
        pytest.skip("INTEGRATION_REDIS_URL is required for the stale-connection proof")
    target = urlparse(raw)
    proxy = _ResetProxy(target.hostname or "127.0.0.1", target.port or 6379)
    port = await proxy.start()
    monkeypatch.setattr(
        revocation_service.settings, "REDIS_URL", f"redis://127.0.0.1:{port}{target.path or '/0'}"
    )
    monkeypatch.setattr(revocation_service, "_redis_client", None)
    yield proxy
    client = revocation_service._redis_client
    if client is not None:
        await client.aclose()
    proxy.close()


@pytest.mark.asyncio
async def test_stale_pooled_connections_do_not_fail_concurrent_revocation_checks(reset_proxy):
    """9/24·9/25: 같은 세션의 동시 확인 3건 중 끊긴 풀 커넥션을 받은 2건이 즉시 503이었다."""
    await asyncio.gather(
        is_admin_session_hash_revoked(TOKEN_HASH), is_admin_session_hash_revoked(TOKEN_HASH)
    )
    assert len(reset_proxy.connections) == 2
    reset_proxy.arm()

    results = await asyncio.gather(
        *(is_admin_session_hash_revoked(TOKEN_HASH) for _ in range(3)), return_exceptions=True
    )

    assert results == [False, False, False]


@pytest.mark.asyncio
async def test_revocation_store_outage_stays_fail_closed(reset_proxy):
    await is_admin_session_hash_revoked(TOKEN_HASH)
    reset_proxy.arm()
    reset_proxy.stop_accepting()

    with pytest.raises(AdminSessionRevocationUnavailable):
        await is_admin_session_hash_revoked(TOKEN_HASH)


@pytest.mark.asyncio
async def test_revocation_client_worst_case_fits_the_bff_three_second_budget(monkeypatch):
    """재연결 1회를 포함한 최악 2 × (연결 + 응답)이 BFF의 3초 확인 예산 안이어야 한다."""
    monkeypatch.setattr(revocation_service, "_redis_client", None)
    client = revocation_service._client()
    try:
        kwargs = client.connection_pool.connection_kwargs
        connect = kwargs["socket_connect_timeout"]
        command = kwargs["socket_timeout"]
        retries = kwargs["retry"].get_retries()

        assert connect and command
        assert (retries + 1) * (connect + command) < 3.0
    finally:
        await client.aclose()
        monkeypatch.setattr(revocation_service, "_redis_client", None)
