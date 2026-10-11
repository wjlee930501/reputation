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


def test_beat_live_fails_when_the_tick_file_is_stale(monkeypatch, tmp_path) -> None:
    import os

    tick = tmp_path / "beat-tick"
    tick.touch()
    os.utime(tick, (1_000.0, 1_000.0))
    monkeypatch.setattr(health_server.worker_liveness, "BEAT_TICK_FILE", tick)
    monkeypatch.setattr(health_server, "_CHECK_BEAT_TICK", True)
    monkeypatch.setattr(health_server, "_STARTED_AT", 0.0)
    monkeypatch.setattr(health_server, "_parent_process_alive", lambda: True)
    monkeypatch.setattr(health_server.settings, "BEAT_LIVENESS_STALE_SECONDS", 600)
    monkeypatch.setattr(health_server.settings, "BEAT_LIVENESS_STARTUP_GRACE_SECONDS", 900)
    monkeypatch.setattr(health_server.time, "time", lambda: 1_000.0 + 601)

    assert health_server.is_live() is False

    os.utime(tick, (1_500.0, 1_500.0))
    assert health_server.is_live() is True


def test_beat_live_passes_inside_startup_grace_while_waiting_for_the_lock(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(health_server.worker_liveness, "BEAT_TICK_FILE", tmp_path / "missing")
    monkeypatch.setattr(health_server, "_CHECK_BEAT_TICK", True)
    monkeypatch.setattr(health_server, "_STARTED_AT", 1_000.0)
    monkeypatch.setattr(health_server, "_parent_process_alive", lambda: True)
    monkeypatch.setattr(health_server.settings, "BEAT_LIVENESS_STALE_SECONDS", 600)
    monkeypatch.setattr(health_server.settings, "BEAT_LIVENESS_STARTUP_GRACE_SECONDS", 900)

    # 옛 Beat의 락이 TTL(300초)까지 남아 tick이 아직 없다 — 유예 안이면 살아 있다.
    monkeypatch.setattr(health_server.time, "time", lambda: 1_000.0 + 400)
    assert health_server.is_live() is True
    monkeypatch.setattr(health_server.time, "time", lambda: 1_000.0 + 901)
    assert health_server.is_live() is False
