import json
from datetime import UTC, datetime, timedelta

import pytest

from app.utils import production_readiness
from app.workers import canary_tasks

EXPECTED_CANARY_ROUTES = {
    "app.workers.canary_tasks.canary_default": "default",
    "app.workers.canary_tasks.canary_content": "content",
    "app.workers.canary_tasks.canary_sov": "sov",
    "app.workers.canary_tasks.canary_reports": "reports",
    "app.workers.canary_tasks.canary_leadgen": "leadgen",
    "app.workers.canary_tasks.canary_certificates": "certificates",
}


def test_every_consumed_queue_has_a_real_canary_task_route() -> None:
    routes = production_readiness.celery_app.conf.task_routes

    assert {
        task_name: routes.get(task_name, {}).get("queue")
        for task_name in EXPECTED_CANARY_ROUTES
    } == EXPECTED_CANARY_ROUTES


def test_readiness_covers_every_declared_schedule_and_routed_task() -> None:
    assert production_readiness.EXPECTED_BEAT_SCHEDULES == set(
        production_readiness.celery_app.conf.beat_schedule
    )
    assert production_readiness.EXPECTED_TASKS == set(
        production_readiness.celery_app.conf.task_routes
    )


def test_real_owner_readiness_excludes_operations_test_accounts() -> None:
    import inspect

    source = inspect.getsource(production_readiness._database_facts)
    assert "is_operations_test IS NOT TRUE" in source


class _CanaryRedis:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def __enter__(self) -> "_CanaryRedis":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)


def _canary_payload(queue: str, revision: str, checked_at: datetime) -> bytes:
    return json.dumps(
        {
            "queue": queue,
            "release_revision": revision,
            "cloud_run_revision": "diagnostic-only",
            "task_id": f"task-{queue}",
            "result": "ok",
            "checked_at": checked_at.isoformat(),
            "checks": {"database": True, "redis": True, "outbox_dry_run": True},
        }
    ).encode()


def test_readiness_names_withheld_queue_then_passes_after_current_canary(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
    revision = "release-task20"
    queue_facts = production_readiness._queue_canary_facts
    monkeypatch.setattr(canary_tasks.settings, "APP_ENV", "production")
    monkeypatch.setattr(canary_tasks.settings, "REPUTATION_RELEASE_REVISION", revision)
    values = {
        canary_tasks.canary_key(revision, queue): _canary_payload(queue, revision, now)
        for queue in canary_tasks.EXPECTED_QUEUES
        if queue != "reports"
    }
    fake = _CanaryRedis(values)
    monkeypatch.setattr(canary_tasks.redis.Redis, "from_url", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(
        production_readiness,
        "_database_facts",
        lambda: {
            "schema_current": True,
            "active_owner_count": 1,
            "hospital_count": 1,
            "live_site_count": 1,
        },
    )
    monkeypatch.setattr(production_readiness, "_redis_ready", lambda: True)
    monkeypatch.setattr(production_readiness, "_workflow_facts", lambda: {"workflow": True})
    monkeypatch.setattr(production_readiness, "_configuration_facts", lambda: {"config": True})

    withheld = production_readiness._queue_canary_facts(now=now)

    assert withheld["queue_canaries_current"] is False
    assert withheld["operator_guidance"]["affected_work"] == ["보고서 생성"]
    assert withheld["developer_contact_info_copy"]["affected_queues"] == ["reports"]
    monkeypatch.setattr(production_readiness, "_queue_canary_facts", lambda: withheld)
    assert production_readiness.build_report()["ready"] is False
    monkeypatch.setattr(production_readiness, "_queue_canary_facts", queue_facts)

    values[canary_tasks.canary_key(revision, "reports")] = _canary_payload(
        "reports", revision, now - timedelta(minutes=14, seconds=59)
    )
    complete = production_readiness._queue_canary_facts(now=now)

    assert complete["queue_canaries_current"] is True
    assert complete["operator_guidance"]["affected_work"] == []
    monkeypatch.setattr(production_readiness, "_queue_canary_facts", lambda: complete)
    assert production_readiness.build_report()["ready"] is True


def test_queue_canary_rejects_a_revision_shorter_than_the_deploy_contract(
    monkeypatch,
) -> None:
    monkeypatch.setattr(canary_tasks.settings, "APP_ENV", "production")
    monkeypatch.setattr(canary_tasks.settings, "REPUTATION_RELEASE_REVISION", "short")

    with pytest.raises(canary_tasks.CanaryConfigurationError):
        canary_tasks.release_revision()


def test_queue_canary_development_fallback_obeys_the_release_contract(monkeypatch) -> None:
    monkeypatch.setattr(canary_tasks.settings, "APP_ENV", "test")
    monkeypatch.setattr(canary_tasks.settings, "REPUTATION_RELEASE_REVISION", "")

    revision = canary_tasks.release_revision()

    assert revision == "local-dev"
    assert canary_tasks.canary_key(revision, "default").endswith(":local-dev:default")


def test_previous_release_or_fifteen_minute_old_canary_never_marks_ready(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
    revision = "release-current"
    monkeypatch.setattr(canary_tasks.settings, "APP_ENV", "production")
    monkeypatch.setattr(canary_tasks.settings, "REPUTATION_RELEASE_REVISION", revision)
    values = {
        canary_tasks.canary_key(revision, queue): _canary_payload(
            queue,
            "release-previous" if queue == "content" else revision,
            now - timedelta(minutes=15, seconds=1) if queue == "sov" else now,
        )
        for queue in canary_tasks.EXPECTED_QUEUES
    }
    fake = _CanaryRedis(values)
    monkeypatch.setattr(canary_tasks.redis.Redis, "from_url", lambda *_args, **_kwargs: fake)

    facts = production_readiness._queue_canary_facts(now=now)

    assert facts["queue_canaries_current"] is False
    assert facts["operator_guidance"]["affected_work"] == ["콘텐츠 운영", "AI 노출 측정"]
    assert facts["developer_contact_info_copy"]["affected_queues"] == ["content", "sov"]


def test_workflow_registry_contains_onboarding_automation() -> None:
    assert all(production_readiness._workflow_facts().values())


def test_build_report_requires_schema_owner_and_runtime_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(
        production_readiness,
        "_database_facts",
        lambda: {
            "schema_current": True,
            "schema_revision": "0031_add_hospital_visual_theme",
            "expected_schema_revision": "0031_add_hospital_visual_theme",
            "active_owner_count": 1,
            "hospital_count": 1,
            "live_site_count": 1,
        },
    )
    monkeypatch.setattr(production_readiness, "_redis_ready", lambda: True)
    monkeypatch.setattr(
        production_readiness,
        "_queue_canary_facts",
        lambda: {"queue_canaries_current": True},
    )
    monkeypatch.setattr(
        production_readiness,
        "_workflow_facts",
        lambda: {
            "required_tasks_registered": True,
            "required_tasks_routed": True,
            "required_schedules_declared": True,
        },
    )
    monkeypatch.setattr(
        production_readiness,
        "_configuration_facts",
        lambda: {
            "generation_keys_configured": True,
            "operator_secrets_configured": True,
            "asset_bucket_configured": True,
            "report_bucket_configured": True,
            "certificate_auto_provisioning_enabled": True,
            "web_search_enabled": True,
        },
    )

    report = production_readiness.build_report()

    assert report["ready"] is True
    assert report["checks"]["active_owner_available"] is True


def test_build_report_fails_closed_without_active_owner(monkeypatch) -> None:
    monkeypatch.setattr(
        production_readiness,
        "_database_facts",
        lambda: {
            "schema_current": True,
            "schema_revision": "0031_add_hospital_visual_theme",
            "expected_schema_revision": "0031_add_hospital_visual_theme",
            "active_owner_count": 0,
            "hospital_count": 0,
            "live_site_count": 0,
        },
    )
    monkeypatch.setattr(production_readiness, "_redis_ready", lambda: True)
    monkeypatch.setattr(
        production_readiness,
        "_queue_canary_facts",
        lambda: {"queue_canaries_current": True},
    )
    monkeypatch.setattr(
        production_readiness,
        "_workflow_facts",
        lambda: {"required_tasks_registered": True},
    )
    monkeypatch.setattr(
        production_readiness,
        "_configuration_facts",
        lambda: {"generation_keys_configured": True},
    )

    report = production_readiness.build_report()

    assert report["ready"] is False
    assert report["checks"]["active_owner_available"] is False
