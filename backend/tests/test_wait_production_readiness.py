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

    assert result == {"ready": True}
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
