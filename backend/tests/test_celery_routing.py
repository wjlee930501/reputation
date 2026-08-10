"""Celery 라우팅 회귀 가드.

task_routes에 없는 태스크는 Celery 기본 "celery" 큐로 떨어져 영원히 실행되지 않는다 —
특히 beat 스케줄 태스크(예: purge_expired_leads = 법적 PII 파기)는 치명적이다.

**소비 큐 목록을 여기 적어두지 않고 docker-entrypoint.sh에서 읽는다.** 하드코딩하면
이 파일이 진실을 *복사*할 뿐이라, 새 큐를 추가하고 워커 인자를 안 고쳐도 두 곳을 같이
고치는 한 통과한다. 실제 배포 인자에서 파싱해야 둘의 드리프트가 실패로 드러난다.
"""
import re
from pathlib import Path

from app.core.celery_app import REDBEAT_SCHEDULE_VERSION, celery_app

_ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"


def _worker_queues_from_entrypoint() -> set[str]:
    text = _ENTRYPOINT.read_text(encoding="utf-8")
    match = re.search(r"-Q\s+([a-z0-9_,\-]+)", text)
    assert match, f"docker-entrypoint.sh에서 celery worker -Q 인자를 찾지 못했다: {_ENTRYPOINT}"
    return {queue.strip() for queue in match.group(1).split(",") if queue.strip()}


KNOWN_WORKER_QUEUES = _worker_queues_from_entrypoint()


def test_worker_queue_list_is_parsed_from_the_real_entrypoint():
    """파싱이 깨져 빈 집합이 되면 아래 검사들이 조용히 무력해진다."""
    assert "default" in KNOWN_WORKER_QUEUES
    assert len(KNOWN_WORKER_QUEUES) >= 4


def test_compose_worker_consumes_the_same_queues_as_the_deployed_entrypoint():
    """로컬(compose)과 배포(entrypoint)가 다른 큐를 소비하면 로컬에서만 되는 태스크가 생긴다."""
    compose = (_ENTRYPOINT.parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(r"celery .*worker.* -Q ([a-z0-9_,\-]+)", compose)
    assert match, "docker-compose.yml에서 celery worker -Q 인자를 찾지 못했다"
    compose_queues = {q.strip() for q in match.group(1).split(",") if q.strip()}
    assert compose_queues == KNOWN_WORKER_QUEUES


def _resolved_queue(task_name: str) -> str | None:
    route = celery_app.conf.task_routes.get(task_name)
    if not isinstance(route, dict):
        return None
    return route.get("queue")


def test_every_beat_task_routes_to_a_consumed_queue():
    beat_schedule = celery_app.conf.beat_schedule
    assert beat_schedule, "beat_schedule must not be empty"

    for entry_name, entry in beat_schedule.items():
        task_name = entry["task"]
        queue = entry.get("options", {}).get("queue") or _resolved_queue(task_name)
        assert queue is not None, (
            f"beat entry '{entry_name}' task '{task_name}' has no task_routes entry — "
            "it would land in the default 'celery' queue, which no worker consumes."
        )
        assert queue in KNOWN_WORKER_QUEUES, (
            f"beat entry '{entry_name}' task '{task_name}' routes to unknown queue "
            f"'{queue}' (workers consume only {sorted(KNOWN_WORKER_QUEUES)})."
        )


def test_all_task_routes_target_consumed_queues():
    for task_name, route in celery_app.conf.task_routes.items():
        assert isinstance(route, dict), f"unexpected route shape for {task_name}: {route!r}"
        queue = route.get("queue")
        assert queue in KNOWN_WORKER_QUEUES, (
            f"task_routes entry '{task_name}' targets queue '{queue}' which no worker "
            f"consumes (workers consume only {sorted(KNOWN_WORKER_QUEUES)})."
        )


def test_pii_purge_task_is_routed():
    # 개인정보보호법 제21조 — 매일 04:00 리드 파기는 반드시 실행 가능해야 한다.
    assert _resolved_queue("app.workers.tasks.purge_expired_leads") == "default"


def test_monthly_reports_close_after_the_next_month_boundary():
    """월간 리포트는 마감 뒤 일주일 동안 자동 재시도한다."""
    schedule = celery_app.conf.beat_schedule["monthly-reports"]["schedule"]
    assert schedule.minute == {15}
    assert schedule.hour == {0, 6, 12, 18}
    assert schedule.day_of_month == set(range(1, 8))


def test_monthly_artifact_incident_reconciliation_runs_each_minute_on_reports_queue():
    task_name = "app.workers.monthly_artifact_reconciliation.reconcile"
    entry = celery_app.conf.beat_schedule["reconcile-monthly-artifact-incidents"]

    assert entry["task"] == task_name
    assert entry["schedule"].minute == set(range(60))
    assert _resolved_queue(task_name) == "reports"
    assert "app.workers.monthly_artifact_reconciliation" in celery_app.conf.include
    assert REDBEAT_SCHEDULE_VERSION >= "2026-08-10.4"


def test_redbeat_refreshes_lock_well_before_ttl_expires():
    """max loop와 lock TTL이 같아 LockNotOwnedError crash loop가 재발하지 않게 한다."""
    max_interval = celery_app.conf.beat_max_loop_interval
    lock_timeout = celery_app.conf.redbeat_lock_timeout

    assert max_interval == 30
    assert lock_timeout >= max_interval * 3
    assert REDBEAT_SCHEDULE_VERSION
