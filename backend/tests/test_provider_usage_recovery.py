"""Provider usage recovery task dispatch and bounded drain contract."""

from app.workers import provider_usage_recovery


def test_drain_requires_signed_dispatch_and_replays_bounded_batch(monkeypatch):
    calls = []

    def require_dispatch(_task, purpose):
        calls.append(("dispatch", purpose))

    async def replay(*, limit):
        calls.append(("replay", limit))
        return {"recovered": 2, "missing": 1, "failed": 0}

    monkeypatch.setattr(provider_usage_recovery, "require_dispatch", require_dispatch)
    monkeypatch.setattr(provider_usage_recovery, "replay_deferred_attempts", replay)

    result = provider_usage_recovery.drain.run()

    assert result == {"recovered": 2, "missing": 1, "failed": 0}
    assert calls == [("dispatch", "drain-provider-usage-spool"), ("replay", 100)]
