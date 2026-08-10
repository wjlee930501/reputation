"""Block a rollout until every current-release queue canary is observable."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from app.utils.production_readiness import build_report

Report = dict[str, Any]


def wait_until_ready(
    *,
    timeout_seconds: float = 900,
    interval_seconds: float = 5,
    build: Callable[[], Report] = build_report,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    started = monotonic()
    last_report: Report = {"ready": False, "checks": {"audit_started": True}}
    while monotonic() - started < timeout_seconds:
        try:
            last_report = build()
        except Exception as exc:  # noqa: BLE001 - rollout gate retries bounded dependency errors.
            last_report = {
                "ready": False,
                "checks": {"audit_completed": False},
                "error_type": type(exc).__name__,
            }
        if last_report.get("ready") is True:
            return last_report
        sleep(interval_seconds)
    return {**last_report, "ready": False, "timed_out": True}


def main() -> int:
    report = wait_until_ready()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("ready") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
