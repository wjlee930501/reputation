"""Read-only production onboarding readiness audit.

Run inside the deployed backend image so the check exercises the same settings,
Secret Manager mounts, database network, Redis network, migration bundle, and
Celery declarations as production::

    python -m app.utils.production_readiness

The command prints booleans/counts only; it never prints credentials or PII.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import redis
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.workers.canary_tasks import read_queue_canaries

EXPECTED_BEAT_SCHEDULES = {
    "canary-content",
    "canary-default",
    "canary-leadgen",
    "canary-reports",
    "canary-sov",
    "canary-certificates",
    "dispatch-notification-outbox",
    "drain-lead-diagnoses",
    "live-custom-domain-health",
    "monthly-reports",
    "monthly-slot-generation",
    "nightly-content-generation",
    "overnight-content-generation-recovery",
    "prepublish-content-generation-recovery",
    "morning-content-auto-publish",
    "project-milestone-events",
    "purge-expired-leads",
    "reconcile-autonomous-workflows",
    "reconcile-essence-snapshots",
    "stranded-content-recovery",
    "reconcile-monthly-artifact-incidents",
    "weekly-naver-source-sync",
    "weekly-sov-monitoring",
}

EXPECTED_TASKS = {
    "app.workers.autonomous_recovery.reconcile",
    "app.workers.content_backlog_recovery.reconcile",
    "app.workers.domain_certificate_tasks.provision_domain_certificate",
    "app.workers.canary_tasks.canary_content",
    "app.workers.canary_tasks.canary_default",
    "app.workers.canary_tasks.canary_leadgen",
    "app.workers.canary_tasks.canary_reports",
    "app.workers.canary_tasks.canary_sov",
    "app.workers.canary_tasks.canary_certificates",
    "app.workers.lead_diagnosis_tasks.build_lead_report",
    "app.workers.lead_diagnosis_tasks.drain_lead_diagnoses",
    "app.workers.lead_diagnosis_tasks.notify_lead_intake",
    "app.workers.lead_diagnosis_tasks.run_lead_diagnosis",
    "app.workers.lead_diagnosis_tasks.send_lead_report_email",
    "app.workers.milestone_event_tasks.project_milestone_events",
    "app.workers.monthly_artifact_reconciliation.reconcile",
    "app.workers.naver_sync.weekly_naver_source_sync",
    "app.workers.notification_tasks.dispatch_notification_outbox",
    "app.workers.tasks.adjust_query_priorities",
    "app.workers.tasks.auto_review_essence_snapshot",
    "app.workers.tasks.build_aeo_site",
    "app.workers.tasks.generate_monthly_report_for_hospital",
    "app.workers.tasks.monitor_live_custom_domains",
    "app.workers.tasks.monthly_slot_generation",
    "app.workers.tasks.morning_content_auto_publish",
    "app.workers.tasks.nightly_content_generation",
    "app.workers.tasks.purge_expired_leads",
    "app.workers.tasks.regenerate_content_item",
    "app.workers.tasks.reconcile_essence_snapshots",
    "app.workers.tasks.retry_site_revalidation",
    "app.workers.tasks.run_monthly_reports",
    "app.workers.tasks.run_sov_for_hospital",
    "app.workers.tasks.run_weekly_monitoring",
    "app.workers.tasks.trigger_v0_report",
}


def _database_facts() -> dict[str, Any]:
    expected_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    with SyncSessionLocal() as db:
        current_head = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        active_owner_count = int(
            db.execute(
                text(
                    "SELECT count(*) FROM admin_users "
                    "WHERE is_active IS TRUE AND role = 'OWNER' "
                    "AND is_operations_test IS NOT TRUE"
                )
            ).scalar_one()
        )
        hospital_count = int(db.execute(text("SELECT count(*) FROM hospitals")).scalar_one())
        live_site_count = int(
            db.execute(text("SELECT count(*) FROM hospitals WHERE site_live IS TRUE")).scalar_one()
        )
    return {
        "schema_current": current_head == expected_head,
        "schema_revision": current_head,
        "expected_schema_revision": expected_head,
        "active_owner_count": active_owner_count,
        "hospital_count": hospital_count,
        "live_site_count": live_site_count,
    }


def _redis_ready() -> bool:
    client = redis.Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    try:
        return bool(client.ping())
    finally:
        client.close()


def _workflow_facts() -> dict[str, bool]:
    # Celery's include list is lazy; importing the task module verifies that the
    # deployed image can load every task and populates the registry used below.
    import app.workers.autonomous_recovery  # noqa: F401, PLC0415
    import app.workers.canary_tasks  # noqa: F401, PLC0415
    import app.workers.content_backlog_recovery  # noqa: F401, PLC0415
    import app.workers.domain_certificate_tasks  # noqa: F401, PLC0415
    import app.workers.lead_diagnosis_tasks  # noqa: F401, PLC0415
    import app.workers.milestone_event_tasks  # noqa: F401, PLC0415
    import app.workers.monthly_artifact_reconciliation  # noqa: F401, PLC0415
    import app.workers.naver_sync  # noqa: F401, PLC0415
    import app.workers.notification_tasks  # noqa: F401, PLC0415
    import app.workers.tasks  # noqa: F401, PLC0415

    registered = set(celery_app.tasks)
    routes = set((celery_app.conf.task_routes or {}).keys())
    schedules = set((celery_app.conf.beat_schedule or {}).keys())
    return {
        "required_tasks_registered": EXPECTED_TASKS <= registered,
        "required_tasks_routed": EXPECTED_TASKS <= routes,
        "required_schedules_declared": EXPECTED_BEAT_SCHEDULES <= schedules,
    }


def _configuration_facts() -> dict[str, bool]:
    project_suffix = f"-{settings.GCP_PROJECT_ID}" if settings.GCP_PROJECT_ID else ""
    return {
        "generation_keys_configured": all(
            (
                settings.ANTHROPIC_API_KEY.strip(),
                settings.OPENAI_API_KEY.strip(),
                settings.GEMINI_API_KEY.strip(),
            )
        ),
        "operator_secrets_configured": all(
            (
                settings.ADMIN_SECRET_KEY.strip(),
                settings.SLACK_WEBHOOK_URL.strip(),
                settings.SITE_BFF_SECRET.strip(),
                settings.SITE_REVALIDATE_SECRET.strip(),
            )
        ),
        "lead_delivery_configured": all(
            (
                settings.LEAD_LOCK_HASH_PEPPER.strip(),
                settings.LEAD_REPORT_TOKEN_SECRET.strip(),
                settings.RESEND_API_KEY.strip(),
                settings.LEAD_MAIL_FROM.strip(),
            )
        )
        and settings.RESEND_API_KEY.strip().lower()
        not in {"placeholder", "replace_me", "changeme"},
        "asset_bucket_configured": bool(project_suffix)
        and settings.GCP_STORAGE_BUCKET.endswith(project_suffix),
        "report_bucket_configured": bool(project_suffix)
        and settings.GCS_REPORTS_BUCKET.endswith(project_suffix),
        "certificate_auto_provisioning_enabled": settings.CERTIFICATE_MANAGER_AUTO_PROVISION,
        "web_search_enabled": settings.OPENAI_CHATGPT_USE_WEB_SEARCH,
        "release_revision_configured": bool(settings.REPUTATION_RELEASE_REVISION.strip())
        or settings.APP_ENV != "production",
    }


def _queue_canary_facts(*, now: datetime | None = None) -> dict[str, Any]:
    canaries = read_queue_canaries(now=now)
    affected_labels = [_queue_operator_label(queue) for queue in canaries.missing_or_stale_queues]
    guidance = (
        {
            "problem": "이번 배포의 자동 작업 준비 확인이 일부 완료되지 않았습니다.",
            "customer_impact": "새 고객 접수, 콘텐츠 운영 또는 보고서 생성이 늦어질 수 있습니다.",
            "next_action": (
                "운영센터에서 준비 상태를 새로고침하세요. 15분 뒤에도 같으면 "
                "‘개발팀 문의용 정보 복사’를 개발팀에 전달하세요."
            ),
            "affected_work": affected_labels,
        }
        if not canaries.current
        else {
            "problem": "확인된 문제가 없습니다.",
            "customer_impact": "현재 고객 운영에 예상되는 영향이 없습니다.",
            "next_action": "별도 조치가 필요하지 않습니다.",
            "affected_work": [],
        }
    )
    return {
        "queue_canaries_current": canaries.current,
        "operator_guidance": guidance,
        "developer_contact_info_copy": {
            "release_revision": canaries.release_revision,
            "affected_queues": list(canaries.missing_or_stale_queues),
            "completed_task_ids": {
                queue: payload["task_id"] for queue, payload in canaries.queue_results.items()
            },
        },
    }


def _queue_operator_label(queue: str) -> str:
    return {
        "default": "기본 자동 작업",
        "content": "콘텐츠 운영",
        "sov": "AI 노출 측정",
        "reports": "보고서 생성",
        "leadgen": "무료 진단 접수",
        "certificates": "도메인 인증서 발급",
    }.get(queue, "자동 작업")


def build_report() -> dict[str, Any]:
    database = _database_facts()
    canaries = _queue_canary_facts()
    checks: dict[str, bool] = {
        "database_connected": True,
        "schema_current": bool(database["schema_current"]),
        "active_owner_available": database["active_owner_count"] > 0,
        "redis_connected": _redis_ready(),
        **_workflow_facts(),
        **_configuration_facts(),
        "queue_canaries_current": bool(canaries["queue_canaries_current"]),
    }
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "facts": {**database, "worker_canaries": canaries},
    }


def main() -> int:
    try:
        report = build_report()
    except Exception as exc:  # noqa: BLE001 - audit must emit a machine-readable failure.
        report = {
            "ready": False,
            "checks": {"audit_completed": False},
            "error_type": type(exc).__name__,
        }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
