"""Read-only production onboarding readiness audit.

Run inside the deployed backend image so the check exercises the same settings,
Secret Manager mounts, database network, Redis network, migration bundle, and
Celery declarations as production::

    python -m app.utils.production_readiness

The command prints booleans/counts only; it never prints credentials or PII.
"""

from __future__ import annotations

import json
from typing import Any

import redis
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal


EXPECTED_BEAT_SCHEDULES = {
    "nightly-content-generation",
    "morning-content-notification",
    "weekly-sov-monitoring",
    "monthly-reports",
    "monthly-slot-generation",
    "purge-expired-leads",
    "live-custom-domain-health",
}

EXPECTED_TASKS = {
    "app.workers.tasks.build_aeo_site",
    "app.workers.tasks.monitor_live_custom_domains",
    "app.workers.tasks.monthly_slot_generation",
    "app.workers.tasks.morning_content_notification",
    "app.workers.tasks.nightly_content_generation",
    "app.workers.tasks.purge_expired_leads",
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
                text("SELECT count(*) FROM admin_users WHERE is_active IS TRUE AND role = 'OWNER'")
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
        "asset_bucket_configured": bool(project_suffix)
        and settings.GCP_STORAGE_BUCKET.endswith(project_suffix),
        "report_bucket_configured": bool(project_suffix)
        and settings.GCS_REPORTS_BUCKET.endswith(project_suffix),
        "certificate_auto_provisioning_enabled": settings.CERTIFICATE_MANAGER_AUTO_PROVISION,
        "web_search_enabled": settings.OPENAI_CHATGPT_USE_WEB_SEARCH,
    }


def build_report() -> dict[str, Any]:
    database = _database_facts()
    checks: dict[str, bool] = {
        "database_connected": True,
        "schema_current": bool(database["schema_current"]),
        "active_owner_available": database["active_owner_count"] > 0,
        "redis_connected": _redis_ready(),
        **_workflow_facts(),
        **_configuration_facts(),
    }
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "facts": database,
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
