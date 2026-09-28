import pytest
from fastapi.testclient import TestClient
from slowapi import Limiter

from app.core import security
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app


class EmptyScalars:
    def all(self):
        return []


class EmptyResult:
    def scalars(self):
        return EmptyScalars()


class FakeDB:
    def __init__(self):
        self.added = []

    async def execute(self, _stmt):
        return EmptyResult()

    # /admin/leads 목록은 PII 대량 열람이라 감사 로그를 쓴다 — rate-limit 검증용 스텁도
    # 그 쓰기 경로를 흉내내야 한다.
    def add(self, item):
        self.added.append(item)

    async def commit(self):
        return None


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


_SESSION_CHECK_PATH = f"/api/v1/admin/auth/sessions/{'a' * 64}/revocation"
_ADMIN_KEY = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture
def session_check_app(monkeypatch):
    """메모리 limiter와 가짜 폐기 저장소로 실제 admin_deps 체인을 태운다."""
    from app.api.admin import auth as auth_api

    async def not_revoked(_token_hash):
        return False

    monkeypatch.setattr(auth_api, "is_admin_session_hash_revoked", not_revoked)
    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter


def test_session_check_is_answered_after_admin_traffic_fills_the_shared_bucket(
    session_check_app,
):
    """9/20: BFF IP 하나의 admin 버킷이 차자 폐기 확인이 429 → BFF가 503으로 닫았다."""
    responses = [session_check_app.get("/api/v1/admin/leads", headers=_ADMIN_KEY)
                 for _ in range(101)]
    assert responses[99].status_code == 200
    assert responses[100].status_code == 429, "공유 admin 버킷은 그대로 분당 100회다"

    check = session_check_app.get(_SESSION_CHECK_PATH, headers=_ADMIN_KEY)

    assert check.status_code == 200
    assert check.json() == {"revoked": False}


def test_session_check_keeps_admin_key_auth_and_its_own_limit(session_check_app):
    assert session_check_app.get(_SESSION_CHECK_PATH).status_code == 401
    assert session_check_app.get(
        _SESSION_CHECK_PATH, headers={"X-Admin-Key": "wrong-key"}
    ).status_code == 401

    statuses = [session_check_app.get(_SESSION_CHECK_PATH, headers=_ADMIN_KEY).status_code
                for _ in range(99)]
    # 앞의 401 두 건도 확인 버킷을 쓴다 — 인증 실패 반복으로 한도를 우회할 수 없다.
    assert statuses[:98] == [200] * 98
    assert statuses[98] == 429


def test_revocation_store_failure_is_a_closed_503_with_a_fixed_json_body(monkeypatch):
    """9/24·9/25 로그의 즉시 503과 같은 형태 — 확인 불가는 열어 주지 않고 닫는다."""
    from app.api.admin import auth as auth_api
    from app.services.admin_session_revocation import AdminSessionRevocationUnavailable

    async def unavailable(_token_hash):
        raise AdminSessionRevocationUnavailable("redis unavailable")

    monkeypatch.setattr(auth_api, "is_admin_session_hash_revoked", unavailable)
    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    try:
        with TestClient(app) as client:
            response = client.get(_SESSION_CHECK_PATH, headers=_ADMIN_KEY)
    finally:
        app.state.limiter = previous_limiter

    assert response.status_code == 503
    assert response.content == b'{"detail":"Admin session revocation state unavailable"}'
    assert len(response.content) == 55
