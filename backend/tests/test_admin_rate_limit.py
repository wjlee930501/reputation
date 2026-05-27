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
