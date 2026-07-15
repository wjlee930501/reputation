"""Celery 라우팅 회귀 가드.

워커는 docker-entrypoint.sh에서 -Q default,content,sov,reports 로만 큐를 소비한다.
task_routes에 없는 태스크는 Celery 기본 "celery" 큐로 떨어져 영원히 실행되지 않는다 —
특히 beat 스케줄 태스크(예: purge_expired_leads = 법적 PII 파기)는 치명적이다.
beat_schedule의 모든 태스크가 워커가 소비하는 큐로 라우팅되는지 검증한다.
"""

from app.core.celery_app import REDBEAT_SCHEDULE_VERSION, celery_app

# docker-entrypoint.sh `celery worker -Q default,content,sov,reports` 와 동기 유지.
KNOWN_WORKER_QUEUES = {"default", "content", "sov", "reports"}


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


def test_monthly_reports_run_at_21_to_avoid_nightly_generation_overlap():
    """월간 리포트는 21:00 — 야간 콘텐츠 생성(23:00)과 워커 슬롯 경합을 피한다 (결함 11)."""
    schedule = celery_app.conf.beat_schedule["monthly-reports"]["schedule"]
    assert 21 in schedule.hour
    assert 23 not in schedule.hour
    # 28~31일 로직 유지
    assert schedule.day_of_month == {28, 29, 30, 31}


def test_redbeat_refreshes_lock_well_before_ttl_expires():
    """max loop와 lock TTL이 같아 LockNotOwnedError crash loop가 재발하지 않게 한다."""
    max_interval = celery_app.conf.beat_max_loop_interval
    lock_timeout = celery_app.conf.redbeat_lock_timeout

    assert max_interval == 30
    assert lock_timeout >= max_interval * 3
    assert REDBEAT_SCHEDULE_VERSION
