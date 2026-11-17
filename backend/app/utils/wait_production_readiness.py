"""Block a rollout until every current-release queue canary is observable.

큐 점검(canary)은 Beat가 5분마다 큐마다 하나씩 보낸다. 점검은 그 작업을 받은 Worker의
릴리스로만 기록되므로 새 릴리스의 Worker가 받아야 통과한다. 그런데 Cloud Run은 교체된
Worker 리비전 인스턴스를 배포 뒤 약 2시간 남겨 두고, 그 인스턴스도 같은 큐를 계속 받는다.
2026-11-17 15:07 배포에서 보고서·무료 진단 큐의 점검 세 번을 모두 옛 Worker가 가져가
15분 대기가 끝나고 자동 롤백됐다. 그래서 기다리는 동안 아직 확인되지 않은 큐의 점검을
30초마다 직접 다시 보낸다 — 점검은 부수효과가 없고(DB `SELECT 1`·Redis 기록), 옛 Worker가
가져간 사본은 옛 릴리스 키에만 쓰여 판정에 섞이지 않는다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from app.utils.production_readiness import build_report
from app.workers.canary_tasks import EXPECTED_QUEUES

Report = dict[str, Any]

logger = logging.getLogger(__name__)

CANARY_NUDGE_INTERVAL_SECONDS = 30.0


def missing_canary_queues(report: Report) -> tuple[str, ...]:
    """아직 이번 릴리스로 확인되지 않은 큐. 보고서 모양이 다르면 빈 값."""
    facts = report.get("facts")
    canaries = facts.get("worker_canaries") if isinstance(facts, dict) else None
    contact = canaries.get("developer_contact_info_copy") if isinstance(canaries, dict) else None
    queues = contact.get("affected_queues") if isinstance(contact, dict) else None
    if not isinstance(queues, list):
        return ()
    return tuple(queue for queue in queues if queue in EXPECTED_QUEUES)


def dispatch_queue_canaries(queues: Sequence[str]) -> None:
    """Beat와 같은 서명 디스패치로 큐 점검을 한 번씩 보낸다."""
    from app.workers import canary_tasks  # noqa: PLC0415
    from app.workers.dispatch_envelope import build_dispatch_headers  # noqa: PLC0415

    for queue in queues:
        task = getattr(canary_tasks, f"canary_{queue}")
        task.apply_async(headers=build_dispatch_headers(f"canary-{queue}"))


def wait_until_ready(
    *,
    timeout_seconds: float = 900,
    interval_seconds: float = 5,
    nudge_interval_seconds: float = CANARY_NUDGE_INTERVAL_SECONDS,
    build: Callable[[], Report] = build_report,
    nudge: Callable[[Sequence[str]], None] = dispatch_queue_canaries,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    started = monotonic()
    last_report: Report = {"ready": False, "checks": {"audit_started": True}}
    last_nudge: float | None = None
    nudges = 0
    while (now := monotonic()) - started < timeout_seconds:
        try:
            last_report = build()
        except Exception as exc:  # noqa: BLE001 - rollout gate retries bounded dependency errors.
            last_report = {
                "ready": False,
                "checks": {"audit_completed": False},
                "error_type": type(exc).__name__,
            }
        if last_report.get("ready") is True:
            return {**last_report, "canary_nudges": nudges}
        queues = missing_canary_queues(last_report)
        if queues and (last_nudge is None or now - last_nudge >= nudge_interval_seconds):
            last_nudge = now
            try:
                nudge(queues)
                nudges += 1
            except Exception as exc:  # noqa: BLE001 - Beat가 보내는 점검이 여전히 남는다.
                logger.warning(
                    "readiness canary nudge failed: %s queues=%s", type(exc).__name__, queues
                )
        sleep(interval_seconds)
    return {**last_report, "ready": False, "timed_out": True, "canary_nudges": nudges}


def main() -> int:
    report = wait_until_ready()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("ready") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
