"""Provider usage recovery task dispatch and bounded drain contract."""

from app.workers import provider_usage_recovery


def test_drain_requires_signed_dispatch_and_replays_bounded_batch(monkeypatch):
    calls = []

    def require_dispatch(_task, purpose):
        calls.append(("dispatch", purpose))

    async def replay(*, limit):
        calls.append(("replay", limit))
        return {"recovered": 2, "missing": 1, "failed": 0}

    async def report_dropped():
        calls.append(("report_dropped",))
        return 0

    monkeypatch.setattr(provider_usage_recovery, "require_dispatch", require_dispatch)
    monkeypatch.setattr(provider_usage_recovery, "replay_deferred_attempts", replay)
    monkeypatch.setattr(provider_usage_recovery, "report_dropped_usage", report_dropped)

    result = provider_usage_recovery.drain.run()

    assert result == {"recovered": 2, "missing": 1, "failed": 0}
    # 복구 뒤 유실 카운터를 사고로 바꾼다(DB가 돌아온 뒤에만 의미가 있다).
    assert calls == [
        ("dispatch", "drain-provider-usage-spool"),
        ("replay", 100),
        ("report_dropped",),
    ]
