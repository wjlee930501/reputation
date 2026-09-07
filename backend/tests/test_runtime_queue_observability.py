from __future__ import annotations

import logging
from types import SimpleNamespace

from app.workers import runtime_queue_observability as queue_observability


def _task(*, queue: str, enqueued_at: object):
    return SimpleNamespace(
        name="app.workers.example",
        request=SimpleNamespace(
            headers={queue_observability.ENQUEUED_AT_HEADER: enqueued_at},
            delivery_info={"routing_key": queue},
        ),
    )


def test_publish_signal_stamps_enqueue_time(monkeypatch) -> None:
    headers: dict[str, object] = {}
    monkeypatch.setattr(queue_observability.time, "time", lambda: 1_700_000_000.25)

    queue_observability.stamp_task_enqueue_time(headers=headers)

    assert headers[queue_observability.ENQUEUED_AT_HEADER] == "1700000000.250000"


def test_queue_wait_rejects_missing_or_invalid_headers_and_clamps_clock_skew() -> None:
    assert queue_observability.queue_wait_seconds(None, now=10.0) is None
    assert queue_observability.queue_wait_seconds({"other": "1"}, now=10.0) is None
    assert (
        queue_observability.queue_wait_seconds(
            {queue_observability.ENQUEUED_AT_HEADER: "bad"}, now=10.0
        )
        is None
    )
    assert (
        queue_observability.queue_wait_seconds(
            {queue_observability.ENQUEUED_AT_HEADER: "11"}, now=10.0
        )
        == 0.0
    )


def test_control_queue_wait_warns_at_its_deadline(monkeypatch, caplog) -> None:
    monkeypatch.setattr(queue_observability.time, "time", lambda: 100.0)

    with caplog.at_level(logging.INFO, logger="app.worker.queue_wait"):
        queue_observability.record_task_queue_wait(
            task=_task(queue="control", enqueued_at="69.5")
        )

    record = caplog.records[-1]
    assert record.levelno == logging.WARNING
    assert record.queue == "control"
    assert record.queue_wait_seconds == 30.5
    assert record.queue_wait_bucket == "lt_1m"
    assert record.queue_wait_threshold_seconds == 30.0


def test_background_queue_short_wait_is_an_info_observation(monkeypatch, caplog) -> None:
    monkeypatch.setattr(queue_observability.time, "time", lambda: 100.0)

    with caplog.at_level(logging.INFO, logger="app.worker.queue_wait"):
        queue_observability.record_task_queue_wait(
            task=_task(queue="sov", enqueued_at="90")
        )

    record = caplog.records[-1]
    assert record.levelno == logging.INFO
    assert record.queue == "sov"
    assert record.queue_wait_seconds == 10.0
    assert record.queue_wait_bucket == "lt_30s"
    assert record.queue_wait_threshold_seconds == 300.0
