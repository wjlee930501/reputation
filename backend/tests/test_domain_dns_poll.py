import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import HTTPException

from app.workers.domain_dns_poll import (
    PendingDomain,
    poll_pending_domains,
    poll_window_expired,
)

NOW = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)


def _target(name: str = "도메인의원") -> PendingDomain:
    return PendingDomain(uuid.uuid4(), name, "www.clinic.example")


def test_window_expires_after_the_configured_days() -> None:
    assert poll_window_expired(NOW - timedelta(days=14), NOW, max_days=14) is True
    assert poll_window_expired(NOW - timedelta(days=13, hours=23), NOW, max_days=14) is False
    # 시작을 모르면(감사행 없음) 만료시키지 않는다.
    assert poll_window_expired(None, NOW, max_days=14) is False


async def test_poll_activates_ready_domains_and_skips_the_rest() -> None:
    ready, waiting, blocked, stale = _target("준비"), _target("대기"), _target("게이트"), _target("만료")
    started = {
        ready.hospital_id: NOW - timedelta(days=1),
        waiting.hospital_id: NOW - timedelta(days=2),
        blocked.hospital_id: NOW - timedelta(days=3),
        stale.hospital_id: NOW - timedelta(days=15),
    }
    verified: list = []
    escalated: list = []
    recovered: list = []

    async def poll_started_at(target):
        return started[target.hospital_id]

    async def verify(target):
        verified.append(target)
        if target is blocked:
            raise HTTPException(status_code=409, detail="gate")
        return SimpleNamespace(dns_verified=target is ready)

    async def escalate(target):
        escalated.append(target)

    async def recover(target):
        recovered.append(target)

    summary = await poll_pending_domains(
        [ready, waiting, blocked, stale], NOW, max_days=14,
        poll_started_at=poll_started_at, verify=verify, escalate=escalate, recover=recover,
    )

    assert verified == [ready, waiting, blocked]  # 만료된 도메인은 더 묻지 않는다
    assert escalated == [stale]
    assert recovered == [ready]
    assert (summary.verified, summary.not_ready, summary.skipped, summary.expired) == (1, 1, 1, 1)


async def test_unexpected_error_on_one_domain_does_not_stop_the_others() -> None:
    first, second = _target(), _target()

    async def poll_started_at(_t):
        return NOW

    async def verify(target):
        if target is first:
            raise RuntimeError("resolver down")
        return SimpleNamespace(dns_verified=True)

    async def noop(_t):
        return None

    summary = await poll_pending_domains(
        [first, second], NOW, max_days=14,
        poll_started_at=poll_started_at, verify=verify, escalate=noop, recover=noop,
    )
    assert (summary.skipped, summary.verified) == (1, 1)


def test_live_domain_monitor_also_polls_pending_domains(monkeypatch) -> None:
    from app.workers import domain_dns_poll, tasks

    called: list = []

    async def fake_once(now):
        called.append(now)
        return domain_dns_poll.DomainPollSummary(verified=1)

    monkeypatch.setattr(tasks, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(domain_dns_poll, "poll_pending_custom_domains_once", fake_once)

    class _NoHospitals:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, _stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _NoHospitals())

    result = tasks.monitor_live_custom_domains.run()

    assert len(called) == 1
    assert result["pending_domains_verified"] == 1


async def test_settled_hospitals_close_their_expired_dns_incident_quietly() -> None:
    from app.workers.domain_dns_poll import recover_settled_dns_incidents

    still_waiting = _target("대기")
    activated_by_hand = uuid.uuid4()
    recovered: list = []

    async def recover(hospital_id, name):
        recovered.append((hospital_id, name))

    closed = await recover_settled_dns_incidents(
        [still_waiting],
        [(still_waiting.hospital_id, "대기"), (activated_by_hand, "수동공개")],
        recover=recover,
    )

    assert closed == 1
    assert recovered == [(activated_by_hand, "수동공개")]
