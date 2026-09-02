"""Celery 라우팅 회귀 가드.

task_routes에 없는 태스크는 Celery 기본 "celery" 큐로 떨어져 영원히 실행되지 않는다 —
특히 beat 스케줄 태스크(예: purge_expired_leads = 법적 PII 파기)는 치명적이다.

**소비 큐 목록을 여기 적어두지 않고 docker-entrypoint.sh에서 읽는다.** 하드코딩하면
이 파일이 진실을 *복사*할 뿐이라, 새 큐를 추가하고 워커 인자를 안 고쳐도 두 곳을 같이
고치는 한 통과한다. 실제 배포 인자에서 파싱해야 둘의 드리프트가 실패로 드러난다.
"""
import importlib
import re
from pathlib import Path

from app.core.celery_app import REDBEAT_SCHEDULE_VERSION, celery_app
from app.workers.dispatch_envelope import PURPOSE_HEADER, expected_purpose

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


def test_every_registered_worker_task_has_a_task_routes_entry():
    """등록된 태스크에 라우팅이 없으면 명시적 queue= 없는 호출이 기본 'celery' 큐로 떨어져
    영원히 실행되지 않는다 — 회귀 사례: generate_content_image, process_source_asset_task,
    recover_lead_diagnosis_measurement/report, backfill_indexnow가 각 호출 지점에서
    queue=를 명시했기 때문에 우연히 동작했다.

    `celery_app.tasks`는 `include=`에 나열된 모듈이 실제로 import돼야 채워지므로,
    여기서 명시적으로 import한다(celery worker 기동 시 로더가 하는 일과 동일).
    """
    for module_name in celery_app.conf.include:
        importlib.import_module(module_name)

    worker_task_names = sorted(
        name for name in celery_app.tasks if name.startswith("app.workers.")
    )
    assert len(worker_task_names) >= 20, (
        "worker task 모듈이 제대로 import되지 않아 이 검사가 무력화됐을 수 있다 "
        f"(registered={len(worker_task_names)})"
    )

    missing = [name for name in worker_task_names if _resolved_queue(name) is None]
    assert not missing, (
        f"task_routes에 없는 워커 태스크: {missing} — 기본 'celery' 큐로 떨어져 실행되지 않는다."
    )


def test_pii_purge_task_is_routed():
    # 개인정보보호법 제21조 — 매일 04:00 리드 파기는 반드시 실행 가능해야 한다.
    assert _resolved_queue("app.workers.tasks.purge_expired_leads") == "default"


def test_domain_certificate_task_is_loaded_and_routed():
    task_name = "app.workers.domain_certificate_tasks.provision_domain_certificate"

    assert "app.workers.domain_certificate_tasks" in celery_app.conf.include
    assert _resolved_queue(task_name) == "certificates"


def test_essence_auto_review_has_immediate_and_periodic_recovery_routes():
    review_task = "app.workers.tasks.auto_review_essence_snapshot"
    reconcile_task = "app.workers.tasks.reconcile_essence_snapshots"
    entry = celery_app.conf.beat_schedule["reconcile-essence-snapshots"]

    assert _resolved_queue(review_task) == "content"
    assert _resolved_queue(reconcile_task) == "default"
    assert entry["task"] == reconcile_task
    assert entry["schedule"].minute == {0, 15, 30, 45}
    assert REDBEAT_SCHEDULE_VERSION >= "2026-08-18.2"


def test_monthly_reports_close_once_on_the_first_day_kst():
    """월간 리포트는 매월 1일 00:15 KST에 한 번만 마감한다."""
    schedule = celery_app.conf.beat_schedule["monthly-reports"]["schedule"]
    assert schedule.minute == {15}
    assert schedule.hour == {0}
    assert schedule.day_of_month == {1}
    assert REDBEAT_SCHEDULE_VERSION >= "2026-09-02.1"


def test_monthly_sov_measurement_runs_only_in_the_month_end_window():
    entry = celery_app.conf.beat_schedule["monthly-sov-measurement"]
    task_name = "app.workers.tasks.run_monthly_sov_measurement"
    purpose = expected_purpose(task_name)

    assert entry["task"] == task_name
    assert purpose == "monthly-sov-measurement"
    assert purpose == entry["options"]["headers"][PURPOSE_HEADER]
    assert entry["schedule"].minute == {0}
    assert entry["schedule"].hour == {0, 6, 12, 18}
    assert entry["schedule"].day_of_month == set(range(24, 32))
    assert _resolved_queue(entry["task"]) == "sov"
    assert REDBEAT_SCHEDULE_VERSION >= "2026-08-30.1"


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
