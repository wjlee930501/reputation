from fastapi.testclient import TestClient

from app.core import database
from app.main import app


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
