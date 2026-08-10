from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.celery_app import celery_app
from app.workers import dispatch_auth, generation_run_control

CANARY_TASKS = {
    "app.workers.canary_tasks.canary_default": "canary-default",
    "app.workers.canary_tasks.canary_content": "canary-content",
    "app.workers.canary_tasks.canary_sov": "canary-sov",
    "app.workers.canary_tasks.canary_reports": "canary-reports",
    "app.workers.canary_tasks.canary_leadgen": "canary-leadgen",
}


def _task(
    headers: dict[str, str] | None,
    *,
    task_id: str = "task-1",
    retries: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(headers=headers, id=task_id, retries=retries)
    )


def test_production_dispatch_requires_a_target_bound_signature(monkeypatch) -> None:
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings, "WORKER_DISPATCH_SECRET", "worker-only-secret-32-bytes-minimum"
    )
    monkeypatch.setattr(dispatch_auth.settings, "REPUTATION_RELEASE_REVISION", "release-a")

    headers = dispatch_auth.stamp_dispatch_headers(
        task_name="app.workers.tasks.regenerate_content_item",
        task_id="task-1",
        args=["content-a"],
        kwargs={},
        retries=0,
        headers=dispatch_auth.build_dispatch_headers("regenerate-content", "content-a"),
        now=1_700_000_000,
    )

    dispatch_auth.require_dispatch(
        _task(headers),
        "regenerate-content",
        "content-a",
        args=["content-a"],
        kwargs={},
        now=1_700_000_001,
    )
    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.require_dispatch(
            _task(headers),
            "regenerate-content",
            "content-b",
            args=["content-b"],
            kwargs={},
            now=1_700_000_001,
        )
    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.require_dispatch(
            _task(None),
            "regenerate-content",
            "content-a",
            args=["content-a"],
            kwargs={},
            now=1_700_000_001,
        )


def test_scheduled_dispatch_cannot_be_reused_for_another_job(monkeypatch) -> None:
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings, "WORKER_DISPATCH_SECRET", "worker-only-secret-32-bytes-minimum"
    )
    monkeypatch.setattr(dispatch_auth.settings, "REPUTATION_RELEASE_REVISION", "release-a")
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name="app.workers.tasks.morning_content_auto_publish",
        task_id="task-2",
        args=[],
        kwargs={},
        retries=0,
        headers=dispatch_auth.build_dispatch_headers("morning-content-auto-publish"),
        now=1_700_000_000,
    )

    dispatch_auth.require_dispatch(
        _task(headers, task_id="task-2"),
        "morning-content-auto-publish",
        args=[],
        kwargs={},
        now=1_700_000_001,
    )
    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.require_dispatch(
            _task(headers, task_id="task-2"),
            "monthly-reports",
            args=[],
            kwargs={},
            now=1_700_000_001,
        )


@pytest.mark.parametrize(("task_name", "purpose"), CANARY_TASKS.items())
def test_queue_canary_stamp_preserves_the_task_local_purpose(
    monkeypatch, task_name: str, purpose: str
) -> None:
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings, "WORKER_DISPATCH_SECRET", "worker-only-secret-32-bytes-minimum"
    )
    monkeypatch.setattr(dispatch_auth.settings, "REPUTATION_RELEASE_REVISION", "release-a")
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name=task_name,
        task_id=f"{purpose}-task",
        args=[],
        kwargs={},
        retries=0,
        headers=dispatch_auth.build_dispatch_headers(purpose),
        now=1_700_000_000,
    )

    dispatch_auth.require_dispatch(
        _task(headers, task_id=f"{purpose}-task"),
        purpose,
        args=[],
        kwargs={},
        now=1_700_000_001,
    )


def test_every_routed_worker_task_is_protected_by_the_authenticated_base() -> None:
    assert issubclass(celery_app.Task, dispatch_auth.AuthenticatedTask)
    for task_name in celery_app.conf.task_routes:
        assert dispatch_auth.is_protected_task(task_name), task_name


def test_operation_run_dispatch_is_stamped_without_caller_supplied_auth(monkeypatch) -> None:
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings, "WORKER_DISPATCH_SECRET", "worker-only-secret-32-bytes-minimum"
    )
    monkeypatch.setattr(dispatch_auth.settings, "REPUTATION_RELEASE_REVISION", "release-a")
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name="app.workers.tasks.trigger_v0_report",
        task_id="operation-task-id",
        args=["hospital-a"],
        kwargs={},
        retries=0,
        headers={"operation_run_id": "run-a"},
        now=1_700_000_000,
    )
    assert headers["operation_run_id"] == "run-a"
    assert headers[dispatch_auth.OPERATION_RUN_HEADER] == "run-a"
    dispatch_auth.require_dispatch(
        _task(headers, task_id="operation-task-id"),
        "trigger-v0-report",
        "hospital-a",
        args=["hospital-a"],
        kwargs={},
        now=1_700_000_001,
    )
    stripped = dict(headers)
    stripped.pop("operation_run_id")
    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.validate_task_dispatch(
            task_name="app.workers.tasks.trigger_v0_report",
            task_id="operation-task-id",
            args=["hospital-a"],
            kwargs={},
            retries=0,
            headers=stripped,
            now=1_700_000_001,
        )


def test_exact_worker_loss_redelivery_is_allowed_but_changed_or_expired_messages_fail(
    monkeypatch,
) -> None:
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings, "WORKER_DISPATCH_SECRET", "worker-only-secret-32-bytes-minimum"
    )
    monkeypatch.setattr(dispatch_auth.settings, "REPUTATION_RELEASE_REVISION", "release-a")
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name="app.workers.tasks.generate_content_image",
        task_id="image-task-id",
        args=["content-a"],
        kwargs={},
        retries=0,
        headers={},
        now=1_700_000_000,
    )
    for _delivery in range(2):
        dispatch_auth.validate_task_dispatch(
            task_name="app.workers.tasks.generate_content_image",
            task_id="image-task-id",
            args=["content-a"],
            kwargs={},
            retries=0,
            headers=headers,
            now=1_700_000_001,
        )
    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.validate_task_dispatch(
            task_name="app.workers.tasks.generate_content_image",
            task_id="image-task-id",
            args=["content-b"],
            kwargs={},
            retries=0,
            headers=headers,
            now=1_700_000_001,
        )
    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.validate_task_dispatch(
            task_name="app.workers.tasks.generate_content_image",
            task_id="another-task-id",
            args=["content-a"],
            kwargs={},
            retries=0,
            headers=headers,
            now=1_700_000_001,
        )
    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.validate_task_dispatch(
            task_name="app.workers.tasks.generate_content_image",
            task_id="image-task-id",
            args=["content-a"],
            kwargs={},
            retries=1,
            headers=headers,
            now=1_700_000_001,
        )
    with pytest.raises(dispatch_auth.DispatchAuthorizationError, match="expired"):
        dispatch_auth.validate_task_dispatch(
            task_name="app.workers.tasks.generate_content_image",
            task_id="image-task-id",
            args=["content-a"],
            kwargs={},
            retries=0,
            headers=headers,
            now=1_700_000_000 + dispatch_auth.DISPATCH_TTL_SECONDS + 1,
        )


def test_non_production_keeps_direct_task_tests_available(monkeypatch) -> None:
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "test")

    dispatch_auth.require_dispatch(_task(None), "monthly-reports")


def test_explicit_generation_run_must_match_the_content_target() -> None:
    run = SimpleNamespace(
        id="run-id",
        operation_type="REGENERATE_CONTENT",
        state="RUNNING",
        task_id="task-id",
        lease_owner="task-id",
        version=3,
        hospital_id="hospital-a",
        request_payload={"source_type": "content_item", "source_id": "content-a"},
    )
    db = SimpleNamespace(get=lambda _model, _run_id: run)
    task = SimpleNamespace(
        request=SimpleNamespace(
            id="task-id",
            headers={"operation_run_id": "44cab52f-b534-4fe0-a9f0-126e903d4abf"},
            operation_run_claim_version=3,
        )
    )

    assert generation_run_control.explicit_run_matches(
        db, task, "content-a", "hospital-a"
    )
    assert not generation_run_control.explicit_run_matches(
        db, task, "content-b", "hospital-a"
    )
    assert not generation_run_control.explicit_run_matches(
        db, task, "content-a", "hospital-b"
    )


def test_operation_run_authorization_binds_image_target_and_task_arguments() -> None:
    hospital_id = "4d147beb-a5f7-4b1b-b564-e16f1f326977"
    content_id = "29263417-a0fd-43de-8a12-6205f5e947e4"
    run = SimpleNamespace(
        operation_type="REGENERATE_CONTENT_IMAGE",
        state="RUNNING",
        task_id="task-id",
        lease_owner="task-id",
        version=3,
        hospital_id=hospital_id,
        request_payload={
            "_dispatch": {
                "target_type": "content_item",
                "target_id": content_id,
                "queue": "content",
                "task_args": [content_id],
            }
        },
    )
    db = SimpleNamespace(get=lambda _model, _run_id: run)
    task = SimpleNamespace(
        request=SimpleNamespace(
            id="task-id",
            headers={"operation_run_id": "44cab52f-b534-4fe0-a9f0-126e903d4abf"},
            operation_run_claim_version=3,
        )
    )

    assert generation_run_control.operation_run_dispatch_authorized(
        db,
        task,
        "app.workers.tasks.generate_content_image",
        [content_id],
    )
    assert not generation_run_control.operation_run_dispatch_authorized(
        db,
        task,
        "app.workers.tasks.generate_content_image",
        ["7af4c873-39a8-45f0-bf3f-8f76fbd2ce25"],
    )


def test_operation_run_authorization_binds_monthly_period_and_rebuild_flag() -> None:
    hospital_id = "4d147beb-a5f7-4b1b-b564-e16f1f326977"
    run = SimpleNamespace(
        operation_type="GENERATE_MONTHLY_REPORT",
        state="RUNNING",
        task_id="task-id",
        lease_owner="task-id",
        version=5,
        hospital_id=hospital_id,
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": hospital_id,
                "queue": "reports",
                "task_args": [hospital_id, 2026, 7, True],
            }
        },
    )
    db = SimpleNamespace(get=lambda _model, _run_id: run)
    task = SimpleNamespace(
        request=SimpleNamespace(
            id="task-id",
            headers={"operation_run_id": "44cab52f-b534-4fe0-a9f0-126e903d4abf"},
            operation_run_claim_version=5,
        )
    )

    assert generation_run_control.operation_run_dispatch_authorized(
        db,
        task,
        "app.workers.tasks.generate_monthly_report_for_hospital",
        [hospital_id, 2026, 7, True],
    )
    assert not generation_run_control.operation_run_dispatch_authorized(
        db,
        task,
        "app.workers.tasks.generate_monthly_report_for_hospital",
        [hospital_id, 2026, 6, True],
    )


@pytest.mark.parametrize(
    ("task_name", "operation_type"),
    [
        (
            "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_measurement",
            "RECOVER_LEAD_MEASUREMENT",
        ),
        (
            "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_report",
            "RECOVER_LEAD_REPORT",
        ),
    ],
)
def test_operation_run_authorization_binds_lead_recovery_attempts(
    task_name: str,
    operation_type: str,
) -> None:
    diagnosis_id = "29263417-a0fd-43de-8a12-6205f5e947e4"
    run = SimpleNamespace(
        operation_type=operation_type,
        state="RUNNING",
        task_id="task-id",
        lease_owner="task-id",
        version=8,
        hospital_id=None,
        request_payload={
            "_dispatch": {
                "target_type": "lead_diagnosis",
                "target_id": diagnosis_id,
                "queue": "leadgen",
                "task_args": [diagnosis_id, 3],
            }
        },
    )
    db = SimpleNamespace(get=lambda _model, _run_id: run)
    task = SimpleNamespace(
        request=SimpleNamespace(
            id="task-id",
            headers={"operation_run_id": "44cab52f-b534-4fe0-a9f0-126e903d4abf"},
            operation_run_claim_version=8,
        )
    )

    assert generation_run_control.operation_run_dispatch_authorized(
        db,
        task,
        task_name,
        [diagnosis_id, 3],
    )
    assert not generation_run_control.operation_run_dispatch_authorized(
        db,
        task,
        task_name,
        [diagnosis_id, 2],
    )


@pytest.mark.parametrize(
    ("task_name", "operation_type"),
    [
        (
            "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_measurement",
            "RECOVER_LEAD_MEASUREMENT",
        ),
        (
            "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_report",
            "RECOVER_LEAD_REPORT",
        ),
    ],
)
def test_production_lead_recovery_reaches_the_worker_only_with_its_exact_run(
    monkeypatch,
    task_name: str,
    operation_type: str,
) -> None:
    from app.core import database

    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings,
        "WORKER_DISPATCH_SECRET",
        "worker-only-secret-32-bytes-minimum",
    )
    monkeypatch.setattr(
        dispatch_auth.settings,
        "REPUTATION_RELEASE_REVISION",
        "release-a",
    )
    diagnosis_id = "29263417-a0fd-43de-8a12-6205f5e947e4"
    run_id = "44cab52f-b534-4fe0-a9f0-126e903d4abf"
    run = SimpleNamespace(
        operation_type=operation_type,
        state="RUNNING",
        task_id="task-id",
        lease_owner="task-id",
        version=8,
        hospital_id=None,
        request_payload={
            "_dispatch": {
                "target_type": "lead_diagnosis",
                "target_id": diagnosis_id,
                "queue": "leadgen",
                "task_args": [diagnosis_id, 3],
            }
        },
    )
    db = SimpleNamespace(get=lambda _model, _run_id: run)

    class SessionContext:
        def __enter__(self):
            return db

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(database, "SyncSessionLocal", SessionContext)
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name=task_name,
        task_id="task-id",
        args=[diagnosis_id, 3],
        kwargs={},
        retries=0,
        headers={"operation_run_id": run_id},
        now=1_700_000_000,
    )
    task = SimpleNamespace(
        name=task_name,
        request=SimpleNamespace(
            id="task-id",
            retries=0,
            headers=headers,
            operation_run_claim_version=8,
        ),
    )

    monkeypatch.setattr(dispatch_auth.time, "time", lambda: 1_700_000_001)
    dispatch_auth.AuthenticatedTask.before_start(
        task,
        "task-id",
        (diagnosis_id, 3),
        {},
    )
