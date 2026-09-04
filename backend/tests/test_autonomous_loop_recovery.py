from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import arrow

from app.core.celery_app import celery_app
from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.operations import Incident, OperationRun, OperationRunState
from app.workers import autonomous_recovery, tasks


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _RecoverySession:
    def __init__(self, *, hospitals=(), runs=(), operation_runs=(), content_items=()):
        self.hospitals = list(hospitals)
        self.runs = list(runs)
        self.operation_runs = list(operation_runs)
        self.content_items = {item.id: item for item in content_items}
        self.added = []
        self.commits = 0
        self._operation_run_reads = 0

    def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is Hospital:
            return _ScalarResult(self.hospitals)
        if entity is OperationRun:
            self._operation_run_reads += 1
            return _ScalarResult(
                self.runs if self._operation_run_reads == 1 else self.operation_runs
            )
        return _ScalarResult(())

    def get(self, entity, row_id):
        if entity is ContentItem:
            return self.content_items.get(row_id)
        return None

    def add(self, value):
        self.added.append(value)

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
    assert routes["app.workers.autonomous_recovery.reconcile"]["queue"] == "default"
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

    assert result == {"site_builds": 1, "site_revalidations": 1, "operation_runs": 0}
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
                "queue": "default",
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

    assert result == {"site_builds": 0, "site_revalidations": 0, "operation_runs": 1}
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

    assert result == {"site_builds": 0, "site_revalidations": 0, "operation_runs": 0}
    assert run.state == OperationRunState.QUEUED
    assert run.queued_at == now - timedelta(minutes=3)
    assert session.commits == 1


def test_reconciler_fails_unsafe_stranded_operation_without_dispatch(monkeypatch) -> None:
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
    dispatched: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(autonomous_recovery, "_now", lambda: now)
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **_kwargs: dispatched.append((name, args)),
    )

    result = autonomous_recovery.reconcile.run()

    assert result == {"site_builds": 0, "site_revalidations": 0, "operation_runs": 0}
    assert dispatched == []
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


def test_reconciler_rejects_monthly_report_rebuild_false(monkeypatch) -> None:
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
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 0
    assert run.state == OperationRunState.FAILED
    assert len(session.added) == 1


def test_reconciler_rejects_monthly_report_extra_args(monkeypatch) -> None:
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
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    result = autonomous_recovery.reconcile.run()

    assert result["operation_runs"] == 0
    assert run.state == OperationRunState.FAILED
    assert len(session.added) == 1


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
