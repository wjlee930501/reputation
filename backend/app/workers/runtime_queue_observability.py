"""Measure broker queue wait before changing worker capacity or service topology."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Final, Protocol

logger = logging.getLogger("app.worker.queue_wait")

ENQUEUED_AT_HEADER: Final = "reputation_queue_enqueued_at"
CONTROL_QUEUE: Final = "control"
CONTROL_WAIT_WARNING_SECONDS: Final = 30.0
BACKGROUND_WAIT_WARNING_SECONDS: Final = 5 * 60.0


class _TaskRequest(Protocol):
    headers: Mapping[str, object] | None
    delivery_info: Mapping[str, object] | None


class _Task(Protocol):
    name: str
    request: _TaskRequest


def stamp_task_enqueue_time(
    *, headers: dict[str, object] | None = None, **_kwargs: object
) -> None:
    """Stamp every publication attempt; Celery retries get their own broker wait."""

    if headers is not None:
        headers[ENQUEUED_AT_HEADER] = f"{time.time():.6f}"


def queue_wait_seconds(headers: Mapping[str, object] | None, *, now: float) -> float | None:
    if not isinstance(headers, Mapping):
        return None
    value = headers.get(ENQUEUED_AT_HEADER)
    try:
        enqueued_at = float(str(value))
    except (TypeError, ValueError):
        return None
    if enqueued_at <= 0:
        return None
    # Small publisher/worker clock skew must not create a negative latency series.
    return max(0.0, now - enqueued_at)


def _observed_queue(task: _Task) -> str:
    delivery = getattr(task.request, "delivery_info", None)
    value = delivery.get("routing_key") if isinstance(delivery, Mapping) else None
    return value if isinstance(value, str) and value else "unknown"


def _bucket(wait_seconds: float) -> str:
    if wait_seconds < 5:
        return "lt_5s"
    if wait_seconds < 30:
        return "lt_30s"
    if wait_seconds < 60:
        return "lt_1m"
    if wait_seconds < 300:
        return "lt_5m"
    return "gte_5m"


def record_task_queue_wait(*, task: _Task | None = None, **_kwargs: object) -> None:
    """Emit one structured observation when a worker starts a broker delivery."""

    if task is None:
        return
    wait_seconds = queue_wait_seconds(task.request.headers, now=time.time())
    if wait_seconds is None:
        return
    queue = _observed_queue(task)
    threshold = (
        CONTROL_WAIT_WARNING_SECONDS if queue == CONTROL_QUEUE else BACKGROUND_WAIT_WARNING_SECONDS
    )
    log = logger.warning if wait_seconds >= threshold else logger.info
    log(
        "Celery queue wait observed task_name=%s queue=%s queue_wait_seconds=%.3f bucket=%s",
        task.name,
        queue,
        wait_seconds,
        _bucket(wait_seconds),
        extra={
            "task_name": task.name,
            "queue": queue,
            "queue_wait_seconds": round(wait_seconds, 3),
            "queue_wait_bucket": _bucket(wait_seconds),
            "queue_wait_threshold_seconds": threshold,
        },
    )
