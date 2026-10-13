import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
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

    # 창이 지난 도메인도 계속 묻는다(DNS 조회는 비용이 없다). 사고는 아직 맞지 않을 때만 연다.
    assert verified == [ready, waiting, blocked, stale]
    assert escalated == [stale]
    assert recovered == [ready]
    assert (summary.verified, summary.not_ready, summary.skipped, summary.expired) == (1, 2, 1, 1)


async def test_expired_domain_that_becomes_ready_activates_and_closes_its_incident() -> None:
    stale = _target("늦게 맞춘 병원")
    escalated: list = []
    recovered: list = []

    async def poll_started_at(_t):
        return NOW - timedelta(days=30)

    async def verify(_t):
        return SimpleNamespace(dns_verified=True)

    async def escalate(target):
        escalated.append(target)

    async def recover(target):
        recovered.append(target)

    summary = await poll_pending_domains(
        [stale], NOW, max_days=14,
        poll_started_at=poll_started_at, verify=verify, escalate=escalate, recover=recover,
    )

    assert escalated == [] and recovered == [stale]
    assert (summary.verified, summary.expired) == (1, 0)


async def test_unknown_window_start_logs_one_warning_and_never_expires(caplog) -> None:
    targets = [_target("a"), _target("b")]
    escalated: list = []

    async def poll_started_at(_t):
        return None

    async def verify(_t):
        return SimpleNamespace(dns_verified=False)

    async def escalate(target):
        escalated.append(target)

    async def noop(_t):
        return None

    with caplog.at_level("WARNING", logger="app.workers.domain_dns_poll"):
        summary = await poll_pending_domains(
            targets, NOW, max_days=14,
            poll_started_at=poll_started_at, verify=verify, escalate=escalate, recover=noop,
        )

    assert escalated == [] and summary.not_ready == 2
    warnings = [r for r in caplog.records if "window start unknown" in r.getMessage()]
    assert len(warnings) == 1


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


def _active(target_or_id, name, domain=None, *, incident_id=None):
    from app.workers.domain_dns_poll import ActiveDnsIncident, PendingDomain, _dedupe_key

    if isinstance(target_or_id, PendingDomain):
        target = target_or_id
    else:
        target = PendingDomain(target_or_id, name, domain)
    if domain is not None:
        target = PendingDomain(target.hospital_id, name, domain)
    return ActiveDnsIncident(incident_id or uuid.uuid4(), target.hospital_id, name, _dedupe_key(target))


async def test_settled_hospitals_close_their_expired_dns_incident_quietly() -> None:
    from app.workers.domain_dns_poll import recover_settled_dns_incidents

    still_waiting = _target("대기")
    activated_by_hand = uuid.uuid4()
    recovered: list = []

    async def recover(hospital_id, name):
        recovered.append((hospital_id, name))

    async def recover_stale(incident_id, name):
        pytest.fail(f"no stale incident expected: {incident_id}")

    closed = await recover_settled_dns_incidents(
        [still_waiting],
        [
            _active(still_waiting, "대기"),
            _active(activated_by_hand, "수동공개", "www.manual.example"),
        ],
        recover=recover,
        recover_stale=recover_stale,
    )

    assert closed == 1
    assert recovered == [(activated_by_hand, "수동공개")]


async def test_a_domain_change_closes_the_old_domains_incident_but_keeps_the_new_one() -> None:
    """사고는 병원·도메인 쌍이다 — 대기 중에 도메인을 바꾸면 옛 도메인의 사고만 닫는다."""
    from app.workers.domain_dns_poll import recover_settled_dns_incidents

    changed = _target("도메인변경")  # 지금 도메인은 www.clinic.example
    old = _active(changed, "도메인변경", "old.clinic.example")
    current = _active(changed, "도메인변경")
    hospitals: list = []
    stale: list = []

    async def recover(hospital_id, name):
        hospitals.append(hospital_id)

    async def recover_stale(incident_id, name):
        stale.append((incident_id, name))

    closed = await recover_settled_dns_incidents(
        [changed], [old, current], recover=recover, recover_stale=recover_stale
    )

    assert closed == 1
    assert hospitals == []
    assert stale == [(old.incident_id, "도메인변경")]


async def _poll_one_expired(verify_outcome):
    stale = _target("만료")
    escalated: list = []

    async def poll_started_at(_t):
        return NOW - timedelta(days=30)

    async def verify(_t):
        if isinstance(verify_outcome, Exception):
            raise verify_outcome
        return verify_outcome

    async def escalate(target):
        escalated.append(target)

    async def noop(_t):
        return None

    summary = await poll_pending_domains(
        [stale], NOW, max_days=14,
        poll_started_at=poll_started_at, verify=verify, escalate=escalate, recover=noop,
    )
    return summary, escalated


@pytest.mark.parametrize(
    "outcome",
    [
        HTTPException(status_code=409, detail="activation gate not ready"),  # DNS는 맞았다
        HTTPException(status_code=409, detail="certificate job in progress"),
        HTTPException(status_code=503, detail="broker unavailable"),
        RuntimeError("resolver crashed"),
    ],
)
async def test_gate_and_infra_errors_never_open_the_dns_incident(outcome) -> None:
    summary, escalated = await _poll_one_expired(outcome)
    assert escalated == []
    assert (summary.skipped, summary.expired, summary.not_ready) == (1, 0, 0)


async def test_an_observed_dns_mismatch_after_the_window_opens_the_incident() -> None:
    summary, escalated = await _poll_one_expired(SimpleNamespace(dns_verified=False))
    assert len(escalated) == 1
    assert (summary.not_ready, summary.expired) == (1, 1)
