import pytest
from fastapi.testclient import TestClient
from slowapi import Limiter

from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.core import security
from app.main import app


class EmptyScalars:
    def all(self):
        return []


class EmptyResult:
    def scalars(self):
        return EmptyScalars()


class FakeDB:
    async def execute(self, _stmt):
        return EmptyResult()


async def override_get_db():
    yield FakeDB()


def test_authenticated_admin_route_applies_slowapi_rate_limit_without_500():
    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/admin/leads",
                headers={"X-Admin-Key": "test-admin-key"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter

    assert response.status_code == 200
    assert response.json() == []


def test_admin_rate_limit_runs_before_missing_key_rejection():
    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")

    try:
        with TestClient(app) as client:
            responses = [client.get("/api/v1/admin/leads") for _ in range(101)]
    finally:
        app.state.limiter = previous_limiter

    assert responses[0].status_code == 401
    assert responses[-1].status_code == 429


async def test_empty_admin_secret_never_authenticates(monkeypatch):
    monkeypatch.setattr(security.settings, "ADMIN_SECRET_KEY", "")

    with pytest.raises(security.HTTPException) as exc:
        await security.verify_admin_key("anything")

    assert exc.value.status_code == 401


def test_login_throttle_pairs_tiered_limits_per_key():
    """#4 — email 키는 IP 키보다 임계값이 커야 한다(원격 락아웃 DoS 방지).

    email 키가 IP 무관 전역이므로 낮은 임계값이면 이메일만 아는 공격자가 소수 실패로
    정상 사용자를 락아웃할 수 있다. 계층형으로 IP는 촘촘하게, email은 느슨하게 짝지운다.
    """
    from types import SimpleNamespace

    from app.api.admin import auth as auth_api

    # email 임계값이 IP 임계값보다 커야 원격 락아웃이 실질적으로 불가능해진다.
    assert auth_api._LOGIN_EMAIL_RATE_LIMIT.amount > auth_api._LOGIN_IP_RATE_LIMIT.amount

    request = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.9"),
        headers=SimpleNamespace(get=lambda *_a, **_k: None),
    )
    limits = auth_api._login_throttle_limits(request, "owner@example.com")
    by_key = {key: limit for limit, key in limits}
    assert by_key["admin-login:email:owner@example.com"] is auth_api._LOGIN_EMAIL_RATE_LIMIT
    assert by_key["admin-login:ip:203.0.113.9"] is auth_api._LOGIN_IP_RATE_LIMIT
