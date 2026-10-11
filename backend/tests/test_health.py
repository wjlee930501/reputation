import redis.asyncio as redis_async
from fastapi.testclient import TestClient

from app.core import database
from app.main import app


class PassingSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, statement):
        return None


class FailingSession:
    async def __aenter__(self):
        raise RuntimeError("db down")

    async def __aexit__(self, exc_type, exc, tb):
        return None


def test_readiness_returns_503_on_database_failure(monkeypatch):
    monkeypatch.setattr(database, "get_async_sessionmaker", lambda: lambda: FailingSession())

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": {"status": "error", "database": "unavailable"}}


class FailingRedis:
    async def ping(self):
        raise RuntimeError("redis down")

    async def aclose(self):
        return None


def test_readiness_returns_503_on_redis_failure(monkeypatch):
    monkeypatch.setattr(database, "get_async_sessionmaker", lambda: lambda: PassingSession())
    monkeypatch.setattr(redis_async, "from_url", lambda *args, **kwargs: FailingRedis())

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": {"status": "error", "redis": "unavailable"}}


def test_lb_liveness_reports_the_release_revision(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "REPUTATION_RELEASE_REVISION", "abc1234def5678")
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    # deploy_smoke.py가 LB 너머 API가 이번 배포 소스인지 이 값으로 확인한다.
    assert response.json() == {"status": "ok", "release": "abc1234def5678"}
