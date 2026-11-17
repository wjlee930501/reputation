from __future__ import annotations

from app.utils import wait_production_readiness


def test_waiter_blocks_release_until_current_canaries_are_ready() -> None:
    reports = iter(({"ready": False}, {"ready": False}, {"ready": True}))
    observed: list[float] = []

    result = wait_production_readiness.wait_until_ready(
        timeout_seconds=30,
        interval_seconds=5,
        build=lambda: next(reports),
        monotonic=iter((0.0, 1.0, 6.0, 11.0)).__next__,
        sleep=observed.append,
    )

    assert result == {"ready": True, "canary_nudges": 0}
    assert observed == [5, 5]


def test_waiter_fails_closed_after_timeout() -> None:
    result = wait_production_readiness.wait_until_ready(
        timeout_seconds=10,
        interval_seconds=5,
        build=lambda: {"ready": False, "checks": {"queue_canaries_current": False}},
        monotonic=iter((0.0, 0.0, 5.0, 10.0)).__next__,
        sleep=lambda _seconds: None,
    )

    assert result["ready"] is False
    assert result["timed_out"] is True


def _missing(*queues: str) -> dict:
    return {
        "ready": False,
        "checks": {"queue_canaries_current": False},
        "facts": {
            "worker_canaries": {
                "developer_contact_info_copy": {"affected_queues": list(queues)},
            }
        },
    }


def test_waiter_resends_missing_canaries_while_old_workers_linger() -> None:
    """옛 Worker가 남아 점검을 가져가도 기다리는 동안 빠진 큐만 다시 보낸다(2026-11-17)."""
    reports = iter(
        (
            _missing("reports", "leadgen"),
            _missing("reports", "leadgen"),
            _missing("leadgen"),
            {"ready": True},
        )
    )
    sent: list[tuple[str, ...]] = []

    result = wait_production_readiness.wait_until_ready(
        timeout_seconds=900,
        interval_seconds=5,
        nudge_interval_seconds=30,
        build=lambda: next(reports),
        nudge=lambda queues: sent.append(tuple(queues)),
        monotonic=iter((0.0, 0.0, 10.0, 31.0, 40.0)).__next__,
        sleep=lambda _seconds: None,
    )

    assert result["ready"] is True
    # 처음 바로, 그리고 30초가 지난 뒤에만 — 5초마다 보내지 않는다.
    assert sent == [("reports", "leadgen"), ("leadgen",)]
    assert result["canary_nudges"] == 2


def test_waiter_ignores_unknown_queues_and_survives_dispatch_failure() -> None:
    def broken(_queues):
        raise RuntimeError("broker down")

    result = wait_production_readiness.wait_until_ready(
        timeout_seconds=10,
        interval_seconds=5,
        build=lambda: _missing("reports", "../evil"),
        nudge=broken,
        monotonic=iter((0.0, 0.0, 5.0, 10.0)).__next__,
        sleep=lambda _seconds: None,
    )

    assert result["timed_out"] is True
    assert result["canary_nudges"] == 0
    assert wait_production_readiness.missing_canary_queues(_missing("sov", "nope")) == ("sov",)


def test_waiter_does_not_resend_when_only_other_checks_fail() -> None:
    sent: list[tuple[str, ...]] = []

    wait_production_readiness.wait_until_ready(
        timeout_seconds=10,
        interval_seconds=5,
        build=lambda: {"ready": False, "checks": {"schema_current": False}},
        nudge=lambda queues: sent.append(tuple(queues)),
        monotonic=iter((0.0, 0.0, 5.0, 10.0)).__next__,
        sleep=lambda _seconds: None,
    )

    assert sent == []


def test_dispatch_sends_signed_canary_to_each_queue(monkeypatch) -> None:
    from app.workers import canary_tasks
    from app.workers.dispatch_envelope import PURPOSE_HEADER

    calls: list[tuple[str, dict]] = []
    for queue in canary_tasks.EXPECTED_QUEUES:
        task = getattr(canary_tasks, f"canary_{queue}")
        monkeypatch.setattr(
            task,
            "apply_async",
            lambda *_a, _q=queue, **kw: calls.append((_q, kw["headers"])),
        )

    wait_production_readiness.dispatch_queue_canaries(("reports", "control"))

    assert [queue for queue, _ in calls] == ["reports", "control"]
    assert calls[0][1][PURPOSE_HEADER] == "canary-reports"
    assert calls[1][1][PURPOSE_HEADER] == "canary-control"
