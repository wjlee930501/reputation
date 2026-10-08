"""Per-instance liveness evidence for the Celery worker consumer.

The worker consumer's ``Heart`` bootstep fires ``heartbeat_sent`` every couple of
seconds from the consumer event loop, and only while the broker connection is up:
when the connection is lost the consumer stops its steps (Heart included) and sits in
its reconnect loop. Touching a local file on every heartbeat therefore records "this
instance's consumer loop ran and was connected" without any shared state. The health
server's ``/live`` endpoint (the Cloud Run liveness probe) reads the file's age so a
consumer that hangs while re-establishing its broker connection is restarted instead of
staying alive and silent for hours (2026-10-08, ``reputation-worker-00223-llc``).
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "reputation-worker-consumer-heartbeat"


def touch_heartbeat(*_args: Any, path: Path | None = None, **_kwargs: Any) -> None:
    """``heartbeat_sent`` receiver. Must never raise into the consumer loop."""
    target = path or HEARTBEAT_FILE
    try:
        target.touch()
    except OSError:
        logger.warning("worker_heartbeat_touch_failed path=%s", target)


def heartbeat_age_seconds(*, path: Path | None = None, now: float | None = None) -> float | None:
    """Seconds since the consumer last sent a heartbeat, or None if it never did."""
    try:
        mtime = os.stat(path or HEARTBEAT_FILE).st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() if now is None else now) - mtime)


def consumer_alive(
    *,
    started_at: float,
    stale_seconds: int,
    startup_grace_seconds: int,
    path: Path | None = None,
    now: float | None = None,
) -> bool:
    """True while the consumer heartbeat is fresh or the instance is still starting up.

    A fresh heartbeat always passes. Without one, the instance passes only inside the
    startup grace window measured from ``started_at`` (the health server's start), so a
    slow first broker connection is not mistaken for a hang. A heartbeat file older than
    ``started_at`` cannot exist in a fresh container; the grace window covers it anyway.
    """
    current = time.time() if now is None else now
    age = heartbeat_age_seconds(path=path, now=current)
    if age is not None and age <= stale_seconds:
        return True
    return current - started_at <= startup_grace_seconds
