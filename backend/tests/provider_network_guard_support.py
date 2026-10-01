"""외부 네트워크 0건 가드 — 공급자(OpenRouter·OpenAI·Anthropic 등) 호출이 새면 테스트가 실패한다.

공급자 SDK는 모두 httpx 위에서 동작하고, httpx는 결국 소켓을 연다. 두 층을 모두 막고
시도 자체를 기록해, 코드가 예외를 삼켜 UNAVAILABLE 같은 정상 경로로 흡수하더라도
teardown에서 0건을 확인한다. 로컬 유닉스 소켓(이벤트 루프 self-pipe 등)과 loopback 주소의
테스트 DB 연결은 막지 않는다.
"""

from __future__ import annotations

import socket

import httpx
import pytest


class ExternalNetworkBlocked(RuntimeError):
    pass


_INET_FAMILIES = (socket.AF_INET, socket.AF_INET6)
# 테스트 전용 로컬 PostgreSQL(asyncpg는 파이썬 소켓으로 붙는다)만 허용한다. 공급자 SDK가 쓰는
# httpx 전송은 주소와 무관하게 모두 막는다.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback(address: object) -> bool:
    host = address[0] if isinstance(address, tuple) and address else address
    return str(host) in _LOOPBACK_HOSTS


# 테스트 모듈은 별칭으로 import하고(ruff F811 회피) 이 이름으로 요청한다.
@pytest.fixture(name="forbid_external_network")
def forbid_external_network(monkeypatch):
    attempts: list[tuple[str, str]] = []

    def _blocked(channel: str, target: object):
        attempts.append((channel, str(target)))
        raise ExternalNetworkBlocked(f"external network call blocked: {channel} {target}")

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection

    def guarded_connect(self, address):
        if self.family in _INET_FAMILIES and not _is_loopback(address):
            _blocked("socket.connect", address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        if self.family in _INET_FAMILIES and not _is_loopback(address):
            _blocked("socket.connect_ex", address)
        return real_connect_ex(self, address)

    def guarded_create_connection(address, *args, **kwargs):
        if not _is_loopback(address):
            _blocked("socket.create_connection", address)
        return real_create_connection(address, *args, **kwargs)

    def guarded_handle_request(self, request):
        _blocked("httpx.HTTPTransport", request.url)

    async def guarded_handle_async_request(self, request):
        _blocked("httpx.AsyncHTTPTransport", request.url)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded_handle_request)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", guarded_handle_async_request
    )

    yield attempts

    assert attempts == [], f"external network calls attempted: {attempts}"
