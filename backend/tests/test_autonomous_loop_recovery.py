from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import arrow

from app.core.celery_app import celery_app
from app.models.content import ContentItem, ContentType
from app.models.hospital import Hospital
from app.models.operations import Incident, OperationRun, OperationRunState
from app.services.image_engine import image_subject_hash
from app.workers import autonomous_recovery, tasks


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _RecoverySession:
    def __init__(
        self,
        *,
        hospitals=(),
        runs=(),
        operation_runs=(),
        content_items=(),
        recertify_candidates=(),
        recertify_runs=(),
    ):
        self.hospitals = list(hospitals)
        self.runs = list(runs)
        self.operation_runs = list(operation_runs)
        self.content_items = {item.id: item for item in content_items}
        self.recertify_candidates = list(recertify_candidates)
        self.recertify_runs = list(recertify_runs)
        self.added = []
        self.commits = 0
        self._operation_run_reads = 0

    def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is Hospital:
            return _ScalarResult(self.hospitals)
        if entity is ContentItem:
            return _ScalarResult(self.recertify_candidates)
        if entity is OperationRun:
            self._operation_run_reads += 1
            # 1: SITE_REVALIDATION, 2: 재배달 후보, 3: 재인증 실행 이력.
            return _ScalarResult(
                (self.runs, self.operation_runs, self.recertify_runs)[
                    min(self._operation_run_reads, 3) - 1
                ]
            )
        return _ScalarResult(())

    def get(self, entity, row_id):
        if entity is ContentItem:
            return self.content_items.get(row_id)
        return None

    def add(self, value):
        self.added.append(value)

    def begin_nested(self):
        return SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_recovery_beat_and_retryable_month_schedules_are_declared() -> None:
    schedules = celery_app.conf.beat_schedule
    routes = celery_app.conf.task_routes

    assert schedules["reconcile-autonomous-workflows"]["task"] == (
        "app.workers.autonomous_recovery.reconcile"
    )
    assert routes["app.workers.autonomous_recovery.reconcile"]["queue"] == "control"
    assert str(schedules["overnight-content-generation-recovery"]["schedule"]) == (
        "<crontab: 0 1,4,7 * * * (m/h/dM/MY/d)>"
    )
    assert str(schedules["prepublish-content-generation-recovery"]["schedule"]) == (
        "<crontab: 45 7 * * * (m/h/dM/MY/d)>"
    )
    assert schedules["overnight-content-generation-recovery"]["task"] == (
        "app.workers.tasks.overnight_content_generation_recovery"
    )
    assert schedules["prepublish-content-generation-recovery"]["task"] == (
        "app.workers.tasks.prepublish_content_generation_recovery"
    )
    assert schedules["overnight-content-generation-recovery"]["options"]["headers"]
    assert schedules["prepublish-content-generation-recovery"]["options"]["headers"]
    assert str(schedules["monthly-slot-generation"]["schedule"]) == (
        "<crontab: 0 */6 25-31 * * (m/h/dM/MY/d)>"
    )
    assert str(schedules["monthly-reports"]["schedule"]) == (
        "<crontab: 15 0 1-7 * * (m/h/dM/MY/d)>"
    )
    assert str(schedules["monthly-report-gap-summary"]["schedule"]) == (
        "<crontab: 0 9 1-7 * * (m/h/dM/MY/d)>"
    )


def test_reconciler_requeues_stranded_site_build_and_revalidation(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital = SimpleNamespace(id=uuid.uuid4())
    run = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_count=0,
        heartbeat_at=now - timedelta(minutes=2),
    )
    session = _RecoverySession(hospitals=(hospital,), runs=(run,))
    dispatched: list[tuple[str, list[str | int], dict[str, object]]] = []

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: dispatched.append((name, args, kwargs)),
    )

    result = autonomous_recovery.reconcile.run()

    assert result == {
        "site_builds": 1,
        "site_revalidations": 1,
        "operation_runs": 0,
        "image_recertifications": 0,
    }
    assert dispatched == [
        (
            "app.workers.tasks.build_aeo_site",
            [str(hospital.id)],
            {
                "queue": "default",
                "headers": autonomous_recovery.build_dispatch_headers(
                    "build-aeo-site", str(hospital.id)
                ),
            },
        ),
        (
            "app.workers.tasks.retry_site_revalidation",
            [str(run.id), 0],
            {
                "queue": "control",
                "priority": 0,
                "headers": autonomous_recovery.build_dispatch_headers(
                    "retry-site-revalidation", str(run.id)
                ),
            },
        ),
    ]
    assert run.heartbeat_at == now
    assert session.commits == 1


def test_reconciler_redispatches_stranded_requested_operation_run(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="REBUILD_SITE",
        state=OperationRunState.REQUESTED,
        hospital_id=hospital_id,
        task_id="lost-before-publish",
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(hospital_id),
                "queue": "default",
                "task_args": [str(hospital_id)],
            }
        },
        requested_at=now - timedelta(minutes=10),
        queued_at=None,
        safe_error_code="BROKER_TIMEOUT",
        safe_error_message="previous dispatch state unknown",
        version=1,
    )
    session = _RecoverySession(operation_runs=(run,))
    dispatched: list[tuple[str, list[str], dict[str, object]]] = []

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: dispatched.append((name, args, kwargs)),
    )

    result = autonomous_recovery.reconcile.run()

    assert result == {
        "site_builds": 0,
        "site_revalidations": 0,
        "operation_runs": 1,
        "image_recertifications": 0,
    }
    assert dispatched == [
        (
            "app.workers.tasks.build_aeo_site",
            [str(hospital_id)],
            {
                "queue": "default",
                "headers": {
                    **autonomous_recovery.build_dispatch_headers(
                        "build-aeo-site",
                        str(hospital_id),
                    ),
                    "operation_run_id": str(run.id),
                },
                "task_id": "lost-before-publish",
            },
        )
    ]
    assert run.state == OperationRunState.QUEUED
    assert run.queued_at == now
    assert run.safe_error_code is None
    assert run.version == 2
    assert session.commits == 1


def test_reconciler_does_not_duplicate_legitimately_queued_operation(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="REBUILD_SITE",
        state=OperationRunState.QUEUED,
        hospital_id=hospital_id,
        task_id="waiting-for-worker-capacity",
        request_payload={},
        requested_at=now - timedelta(minutes=10),
        queued_at=now - timedelta(minutes=3),
    )
    session = _RecoverySession(operation_runs=(run,))

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a healthy queued task must not be duplicated")
        ),
    )

    result = autonomous_recovery.reconcile.run()

    assert result == {
        "site_builds": 0,
        "site_revalidations": 0,
        "operation_runs": 0,
        "image_recertifications": 0,
    }
    assert run.state == OperationRunState.QUEUED
    assert run.queued_at == now - timedelta(minutes=3)
    assert session.commits == 1


def test_reconciler_requeues_only_expired_running_v0_with_same_lineage(monkeypatch) -> None:
    now = datetime(2026, 9, 8, 3, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="TRIGGER_V0_REPORT",
        state=OperationRunState.RUNNING,
        hospital_id=hospital_id,
        task_id="hard-killed-v0-task",
        request_payload={},
        requested_at=now - timedelta(hours=2),
        queued_at=now - timedelta(hours=2),
        heartbeat_at=now - timedelta(hours=1),
        lease_owner="hard-killed-v0-task",
        lease_expires_at=now - timedelta(seconds=1),
        completed_at=None,
        safe_error_code=None,
        safe_error_message=None,
        version=7,
    )
    session = _RecoverySession(operation_runs=(run,))
    dispatched = []
    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: dispatched.append((name, args, kwargs)),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 1
    assert dispatched == [
        (
            "app.workers.tasks.trigger_v0_report",
            [str(hospital_id)],
            {
                "queue": "reports",
                "headers": {
                    **autonomous_recovery.build_dispatch_headers(
                        "trigger-v0-report", str(hospital_id)
                    ),
                    "operation_run_id": str(run.id),
                },
                "task_id": "hard-killed-v0-task",
            },
        )
    ]
    assert run.state == OperationRunState.QUEUED
    assert run.lease_owner is None
    assert run.lease_expires_at is None
    assert run.version == 8


def test_reconciler_does_not_take_over_live_running_v0(monkeypatch) -> None:
    now = datetime(2026, 9, 8, 3, 0, tzinfo=UTC)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="TRIGGER_V0_REPORT",
        state=OperationRunState.RUNNING,
        hospital_id=uuid.uuid4(),
        task_id="live-v0-task",
        request_payload={},
        requested_at=now - timedelta(hours=2),
        queued_at=now - timedelta(hours=2),
        heartbeat_at=now,
        lease_owner="live-v0-task",
        lease_expires_at=now + timedelta(seconds=1),
    )
    session = _RecoverySession(operation_runs=(run,))
    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a live V0 lease must not be duplicated")
        ),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 0
    assert run.state == OperationRunState.RUNNING


def test_reconciler_rebuilds_unsafe_stored_dispatch_from_hospital_truth(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="REBUILD_SITE",
        state=OperationRunState.REQUESTED,
        hospital_id=hospital_id,
        task_id="unsafe-dispatch",
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(uuid.uuid4()),
                "queue": "default",
                "task_args": [str(hospital_id)],
            }
        },
        requested_at=now - timedelta(minutes=10),
        queued_at=None,
        safe_error_code=None,
        safe_error_message=None,
        version=1,
    )
    session = _RecoverySession(operation_runs=(run,))
    dispatched: list[tuple[str, list[str], dict[str, object]]] = []

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: dispatched.append((name, args, kwargs)),
    )

    result = autonomous_recovery.reconcile.run()

    assert result == {
        "site_builds": 0,
        "site_revalidations": 0,
        "operation_runs": 1,
        "image_recertifications": 0,
    }
    assert dispatched == [
        (
            "app.workers.tasks.build_aeo_site",
            [str(hospital_id)],
            {
                "queue": "default",
                "headers": {
                    **autonomous_recovery.build_dispatch_headers(
                        "build-aeo-site", str(hospital_id)
                    ),
                    "operation_run_id": str(run.id),
                },
                "task_id": "unsafe-dispatch",
            },
        )
    ]
    assert run.state == OperationRunState.QUEUED
    assert run.safe_error_code is None
    assert run.completed_at is None
    assert run.version == 2
    assert session.added == []


def test_reconciler_fails_unrebuildable_dispatch_with_incident_and_open_intent(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.REQUESTED,
        hospital_id=hospital_id,
        task_id="unsafe-monthly-dispatch",
        request_payload={"_dispatch": {"task_args": [str(uuid.uuid4())]}},
        result_summary={},
        requested_at=now - timedelta(minutes=10),
        queued_at=None,
        safe_error_code=None,
        safe_error_message=None,
        version=1,
    )
    session = _RecoverySession(operation_runs=(run,))
    intents = []

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )
    monkeypatch.setattr(
        autonomous_recovery,
        "enqueue_notification_sync",
        lambda _db, intent, **_kwargs: intents.append(intent),
    )

    result = autonomous_recovery.reconcile.run()

    assert result == {
        "site_builds": 0,
        "site_revalidations": 0,
        "operation_runs": 0,
        "image_recertifications": 0,
    }
    assert run.state == OperationRunState.FAILED
    assert run.safe_error_code == "UNSAFE_STORED_DISPATCH"
    assert run.completed_at == now
    assert run.version == 2
    assert len(session.added) == 1
    incident = session.added[0]
    assert isinstance(incident, Incident)
    assert incident.state == "OPEN"
    assert incident.operation_run_id == run.id
    assert incident.safe_error_code == "UNSAFE_STORED_DISPATCH"
    assert incident.hospital_id == hospital_id
    assert len(intents) == 1
    assert intents[0].notification_type == "INCIDENT_OPEN"
    assert intents[0].channel == "SLACK_DEV"
    assert intents[0].incident_id == incident.id
    assert intents[0].operation_run_id == run.id


def test_reconciler_allows_monthly_report_period_dispatch(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.REQUESTED,
        hospital_id=hospital_id,
        task_id="monthly-period-dispatch",
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(hospital_id),
                "queue": "reports",
                "task_args": [str(hospital_id), 2026, 7],
            }
        },
        requested_at=now - timedelta(minutes=10),
        queued_at=None,
        safe_error_code=None,
        safe_error_message=None,
        version=1,
    )
    session = _RecoverySession(operation_runs=(run,))
    dispatched: list[tuple[str, list[object], dict[str, object]]] = []

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: dispatched.append((name, args, kwargs)),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 1
    assert dispatched == [
        (
            "app.workers.tasks.generate_monthly_report_for_hospital",
            [str(hospital_id), 2026, 7],
            {
                "queue": "reports",
                "headers": {
                    **autonomous_recovery.build_dispatch_headers(
                        "app.workers.tasks.generate_monthly_report_for_hospital",
                        str(hospital_id),
                    ),
                    "operation_run_id": str(run.id),
                },
                "task_id": "monthly-period-dispatch",
            },
        )
    ]


def test_reconciler_rebuilds_monthly_dispatch_from_summary_and_replaces_missing_task_id(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.REQUESTED,
        hospital_id=hospital_id,
        task_id=None,
        request_payload={"_dispatch": {"task_args": "corrupt"}},
        result_summary={"period_year": 2026, "period_month": 7},
        requested_at=now - timedelta(minutes=10),
        queued_at=None,
        completed_at=None,
        safe_error_code="BROKER_TIMEOUT",
        safe_error_message="previous dispatch state unknown",
        version=1,
    )
    session = _RecoverySession(operation_runs=(run,))
    dispatched = []
    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: dispatched.append((name, args, kwargs)),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 1
    assert dispatched[0][1] == [str(hospital_id), 2026, 7]
    assert dispatched[0][2]["task_id"] == run.task_id
    assert uuid.UUID(run.task_id)
    assert run.state == OperationRunState.QUEUED
    assert run.safe_error_code is None
    assert session.added == []


def test_reconciler_allows_monthly_report_rebuild_true_dispatch(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.QUEUED,
        hospital_id=hospital_id,
        task_id="monthly-rebuild-dispatch",
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(hospital_id),
                "queue": "reports",
                "task_args": [str(hospital_id), 2026, 7, True],
            }
        },
        requested_at=now - timedelta(hours=2),
        queued_at=now - timedelta(hours=2),
        safe_error_code=None,
        safe_error_message=None,
        version=3,
    )
    session = _RecoverySession(operation_runs=(run,))
    dispatched: list[list[object]] = []

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda _name, args, **_kwargs: dispatched.append(args),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 1
    assert dispatched == [[str(hospital_id), 2026, 7, True]]
    assert run.state == OperationRunState.QUEUED
    assert run.version == 4
    assert session.added == []


def test_reconciler_allows_monthly_sov_dispatch(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="RUN_SOV",
        state=OperationRunState.REQUESTED,
        hospital_id=hospital_id,
        task_id="monthly-sov-dispatch",
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(hospital_id),
                "queue": "sov",
                "task_args": [str(hospital_id), "monthly", 2026, 8],
            }
        },
        requested_at=now - timedelta(minutes=10),
        queued_at=None,
        safe_error_code=None,
        safe_error_message=None,
        version=1,
    )
    session = _RecoverySession(operation_runs=(run,))
    dispatched: list[list[object]] = []

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda _name, args, **_kwargs: dispatched.append(args),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 1
    assert dispatched == [[str(hospital_id), "monthly", 2026, 8]]
    assert run.state == OperationRunState.QUEUED
    assert run.safe_error_code is None
    assert session.added == []


def test_reconciler_allows_monthly_report_coverage_recovery_dispatch(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.QUEUED,
        hospital_id=hospital_id,
        task_id="monthly-coverage-recovery-dispatch",
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(hospital_id),
                "queue": "reports",
                "task_args": [str(hospital_id), 2026, 7, True, True],
            }
        },
        requested_at=now - timedelta(hours=2),
        queued_at=now - timedelta(hours=2),
        safe_error_code=None,
        safe_error_message=None,
        version=1,
    )
    session = _RecoverySession(operation_runs=(run,))
    dispatched: list[list[object]] = []

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda _name, args, **_kwargs: dispatched.append(args),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 1
    assert dispatched == [[str(hospital_id), 2026, 7, True, True]]
    assert run.state == OperationRunState.QUEUED
    assert run.safe_error_code is None
    assert session.added == []


def test_reconciler_rebuilds_monthly_report_dispatch_without_false_flag(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.REQUESTED,
        hospital_id=hospital_id,
        task_id="monthly-false-rebuild",
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(hospital_id),
                "queue": "reports",
                "task_args": [str(hospital_id), 2026, 7, False],
            }
        },
        requested_at=now - timedelta(minutes=10),
        queued_at=None,
        safe_error_code=None,
        safe_error_message=None,
        version=1,
    )
    session = _RecoverySession(operation_runs=(run,))

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    dispatched = []
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda _name, args, **_kwargs: dispatched.append(args),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 1
    assert dispatched == [[str(hospital_id), 2026, 7]]
    assert run.state == OperationRunState.QUEUED
    assert session.added == []


def test_reconciler_drops_unsafe_monthly_report_extra_args(monkeypatch) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.REQUESTED,
        hospital_id=hospital_id,
        task_id="monthly-extra-arg",
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(hospital_id),
                "queue": "reports",
                "task_args": [str(hospital_id), 2026, 7, True, 1],
            }
        },
        requested_at=now - timedelta(minutes=10),
        queued_at=None,
        safe_error_code=None,
        safe_error_message=None,
        version=1,
    )
    session = _RecoverySession(operation_runs=(run,))

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    dispatched = []
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda _name, args, **_kwargs: dispatched.append(args),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 1
    assert dispatched == [[str(hospital_id), 2026, 7, True]]
    assert run.state == OperationRunState.QUEUED
    assert session.added == []


def test_monthly_slot_reconciliation_runs_after_the_twenty_fifth(monkeypatch) -> None:
    observed = arrow.get(2026, 8, 27, 6, 0, 0).to("Asia/Seoul")
    session = _RecoverySession()

    monkeypatch.setattr(tasks.arrow, "now", lambda _zone: observed)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: session)

    tasks.monthly_slot_generation.run()

    assert session.commits == 1


def test_failed_scheduled_monthly_run_is_reclaimed_automatically() -> None:
    hospital = SimpleNamespace(id=uuid.uuid4())
    failed = SimpleNamespace(
        id=uuid.uuid4(),
        state=OperationRunState.FAILED,
        attempt_count=1,
        heartbeat_at=None,
        started_at=datetime.now(UTC) - timedelta(hours=2),
        requested_at=datetime.now(UTC) - timedelta(hours=2),
        completed_at=datetime.now(UTC) - timedelta(hours=1),
        total_count=1,
        success_count=0,
        failure_count=1,
        skipped_count=0,
        safe_error_code="MONTHLY_REPORT_FAILED",
        safe_error_message="failed",
        result_summary={"stage": "FAILED"},
        version=2,
    )

    class _MonthlySession:
        commits = 0

        def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: failed)

        def commit(self):
            self.commits += 1

    session = _MonthlySession()
    run_id, replayed = tasks._start_scheduled_monthly_operation_run(
        session,
        hospital,
        arrow.get(2026, 7, 31, 23, 59, 59).to("Asia/Seoul"),
    )

    assert run_id == failed.id
    assert replayed is False
    assert failed.state == OperationRunState.RUNNING
    assert failed.attempt_count == 2
    assert failed.safe_error_code is None
    assert failed.completed_at is None
    assert session.commits == 1


# ── 공개 글 이미지 재인증 backstop (H-01) ─────────────────────────────────

_RECERTIFY_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _withheld_item(revision: int = 3, title: str = "치질 증상"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        content_revision=revision,
        content_type=ContentType.DISEASE,
        title=title,
    )


def _recertify_run(
    item,
    *,
    state,
    safe_error_code=None,
    title=None,
    finished_minutes_ago=60,
    active_minutes_ago=1,
):
    terminal = state not in (
        OperationRunState.REQUESTED,
        OperationRunState.QUEUED,
        OperationRunState.RUNNING,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="RECERTIFY_PUBLISHED_IMAGE",
        state=state,
        safe_error_code=safe_error_code,
        request_payload={
            "source_id": str(item.id),
            "subject_hash": image_subject_hash(
                item.content_type, item.title if title is None else title
            ),
        },
        completed_at=(
            _RECERTIFY_NOW - timedelta(minutes=finished_minutes_ago) if terminal else None
        ),
        heartbeat_at=None,
        started_at=None,
        queued_at=None,
        requested_at=_RECERTIFY_NOW - timedelta(minutes=active_minutes_ago),
    )


def _run_recertify_sweep(monkeypatch, session):
    dispatched: list[tuple[str, list[str], dict[str, object]]] = []
    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: _RECERTIFY_NOW)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: dispatched.append((name, args, kwargs)),
    )
    return autonomous_recovery.reconcile.run(), dispatched


def test_recertify_sweep_redispatches_a_cleared_certificate(monkeypatch) -> None:
    item = _withheld_item()
    session = _RecoverySession(recertify_candidates=(item,))

    result, dispatched = _run_recertify_sweep(monkeypatch, session)

    assert result["image_recertifications"] == 1
    name, args, kwargs = dispatched[0]
    assert name == "app.workers.tasks.recertify_published_content_image"
    assert args == [str(item.id)]
    assert kwargs["queue"] == "content"
    run = session.added[0]
    assert run.operation_type == "RECERTIFY_PUBLISHED_IMAGE"
    assert run.state == OperationRunState.REQUESTED
    subject = image_subject_hash(item.content_type, item.title)
    assert run.idempotency_key == f"recertify:{item.id}:{subject[:16]}:s1"
    # 시도 수는 키가 아니라 payload에 적힌 subject로 센다.
    assert run.request_payload["subject_hash"] == subject
    assert run.request_payload["revision"] == 3
    assert kwargs["headers"]["operation_run_id"] == str(run.id)
    assert kwargs["task_id"] == run.task_id


def test_recertify_sweep_leaves_an_in_flight_run_alone(monkeypatch) -> None:
    item = _withheld_item()
    session = _RecoverySession(
        recertify_candidates=(item,),
        recertify_runs=(_recertify_run(item, state=OperationRunState.RUNNING),),
    )

    result, dispatched = _run_recertify_sweep(monkeypatch, session)

    assert result["image_recertifications"] == 0
    assert dispatched == [] and session.added == []


def test_recertify_sweep_ignores_a_stranded_run_and_an_older_subject(monkeypatch) -> None:
    """좌초한 실행과 지난 제목의 실행은 지금 제목의 자동 복구를 막지 않는다.

    좌초한 실행은 이미 샀을 수 있어 예산으로는 세지만, 다음 실행을 막지는 않는다.
    """
    item = _withheld_item()
    session = _RecoverySession(
        recertify_candidates=(item,),
        recertify_runs=(
            # 하드 제한(900초)을 한참 넘긴 RUNNING — 유실된 실행이다.
            _recertify_run(
                item, state=OperationRunState.RUNNING, active_minutes_ago=120
            ),
            # 지난 제목의 진행 중 실행 — 시작하자마자 현재 subject를 보고 끝난다.
            _recertify_run(item, state=OperationRunState.QUEUED, title="옛 제목"),
        ),
    )

    result, _dispatched = _run_recertify_sweep(monkeypatch, session)

    assert result["image_recertifications"] == 1
    # 좌초한 실행이 시도 하나를 이미 썼으므로 다음 키는 s2다.
    assert session.added[0].idempotency_key.endswith(":s2")


def test_recertify_sweep_stops_at_an_operator_required_rejection(monkeypatch) -> None:
    """거절은 사람의 결정이다 — 다시 사도 같은 답이 나온다."""
    item = _withheld_item()
    session = _RecoverySession(
        recertify_candidates=(item,),
        recertify_runs=(
            _recertify_run(
                item,
                state=OperationRunState.FAILED,
                safe_error_code="PUBLISHED_IMAGE_RECERTIFY_REJECTED",
            ),
        ),
    )

    result, dispatched = _run_recertify_sweep(monkeypatch, session)

    assert result["image_recertifications"] == 0
    assert dispatched == [] and session.added == []


def test_recertify_sweep_waits_out_the_cooldown(monkeypatch) -> None:
    """방금 끝난 실패 위에 곧바로 다음 시도를 얹지 않는다 — 예산이 몇 분에 타버린다."""
    item = _withheld_item()
    session = _RecoverySession(
        recertify_candidates=(item,),
        recertify_runs=(
            _recertify_run(
                item,
                state=OperationRunState.FAILED,
                safe_error_code="COST_BLOCKED",
                finished_minutes_ago=5,
            ),
        ),
    )

    result, dispatched = _run_recertify_sweep(monkeypatch, session)

    assert result["image_recertifications"] == 0
    assert dispatched == []


def test_recertify_sweep_redispatches_a_failure_the_task_never_reported(monkeypatch) -> None:
    """태스크가 아예 시작하지 못한 실패도 예산 안에서 다시 이어간다."""
    item = _withheld_item()
    session = _RecoverySession(
        recertify_candidates=(item,),
        recertify_runs=(
            _recertify_run(
                item, state=OperationRunState.FAILED, safe_error_code="BROKER_UNAVAILABLE"
            ),
        ),
    )

    result, _dispatched = _run_recertify_sweep(monkeypatch, session)

    assert result["image_recertifications"] == 1
    subject = image_subject_hash(item.content_type, item.title)
    assert session.added[0].idempotency_key == f"recertify:{item.id}:{subject[:16]}:s2"


def test_recertify_sweep_records_the_spent_budget_once_and_then_stops(monkeypatch) -> None:
    """예산이 끝나면 유료 호출 없는 마지막 실행 하나만 더 만들고 멈춘다."""
    item = _withheld_item()
    spent = [
        _recertify_run(
            item, state=OperationRunState.FAILED, safe_error_code="PROVIDER_UNAVAILABLE"
        )
        for _ in range(3)
    ]
    session = _RecoverySession(recertify_candidates=(item,), recertify_runs=spent)

    result, _dispatched = _run_recertify_sweep(monkeypatch, session)

    assert result["image_recertifications"] == 1

    closed = _RecoverySession(
        recertify_candidates=(item,),
        recertify_runs=(
            *spent,
            _recertify_run(
                item,
                state=OperationRunState.FAILED,
                safe_error_code="PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED",
            ),
        ),
    )

    exhausted, no_dispatch = _run_recertify_sweep(monkeypatch, closed)

    assert exhausted["image_recertifications"] == 0
    assert no_dispatch == [] and closed.added == []
