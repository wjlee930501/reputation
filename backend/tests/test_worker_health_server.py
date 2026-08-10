from __future__ import annotations

from app.workers import health_server


def test_readiness_fails_when_celery_parent_is_dead(monkeypatch) -> None:
    monkeypatch.setattr(health_server, "_parent_process_alive", lambda: False)
    monkeypatch.setattr(health_server, "_database_ready", lambda: True)
    monkeypatch.setattr(health_server, "_redis_ready", lambda: True)

    assert health_server.readiness_checks() == {
        "celery_parent_alive": False,
        "database_connected": True,
        "redis_connected": True,
        "release_revision_configured": True,
    }
    assert health_server.is_ready() is False


def test_production_readiness_requires_release_and_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(health_server.settings, "APP_ENV", "production")
    monkeypatch.setattr(health_server.settings, "REPUTATION_RELEASE_REVISION", "")
    monkeypatch.setattr(health_server, "_parent_process_alive", lambda: True)
    monkeypatch.setattr(health_server, "_database_ready", lambda: True)
    monkeypatch.setattr(health_server, "_redis_ready", lambda: True)

    assert health_server.is_ready() is False

    monkeypatch.setattr(
        health_server.settings, "REPUTATION_RELEASE_REVISION", "release-20260810"
    )
    assert health_server.is_ready() is True
