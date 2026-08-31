"""Pin the 2026-08-31 month-end visibility-measurement repeat-alert cut."""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.celery_app import celery_app
from app.models.hospital import HospitalStatus
from app.models.operations import OperationRunState
from app.services.monthly_period import (
    is_august_2026_conversion_window,
    scheduled_report_period,
)
from app.services.sov_tracking_set import CONVERSION_HOSPITAL_NAME_TOKENS
from app.workers import task_incident_control, tasks, weekly_sov_incident_control
from app.workers.tasks import ManifestError

# ── 1. terminal failures must not Celery-retry ──────────────────────────────


def test_terminal_measurement_failures_return_instead_of_raising_retry():
    source = Path(tasks.__file__).read_text(encoding="utf-8")
    for message in (
        "weekly_sov_cost_guard_blocked",
        "weekly_sov_no_measurement_manifest",
        "weekly_sov_measurement_policy_drift",
        "weekly_sov_measurement_partial",
        "weekly_sov_unresolved_manifest_state",
    ):
        assert f"raise RuntimeError({message!r})" not in source
    assert "self.retry" in inspect.getsource(tasks.run_sov_for_hospital)
    assert "_finish_sov_operation_run(" in inspect.getsource(tasks.run_sov_for_hospital)


def _call_run_sov(task, hospital_id: str) -> None:
    body = getattr(tasks.run_sov_for_hospital.run, '__func__', tasks.run_sov_for_hospital.run)
    body(task, hospital_id)


def _empty_result():
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


class _SovTaskDB:
    def __init__(self, hospital):
        self.hospital = hospital
        self.commits = 0

    def get(self, _model, _id):
        return self.hospital

    def execute(self, _stmt):
        return _empty_result()

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _patch_sov_task_shell(monkeypatch, hospital, *, measurement_mode="weekly"):
    retries = []
    finished = []
    recorded = []

    def fake_retry(*, exc=None, countdown=None):
        retries.append({"exc": exc, "countdown": countdown})
        raise RuntimeError("retry-called")

    task = SimpleNamespace(
        retry=fake_retry,
        request=SimpleNamespace(
            headers={},
            id="worker-task",
            operation_run_claim_version=1,
        ),
    )
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _SovTaskDB(hospital))
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "_operation_run_claimed_or_legacy", lambda *_args: True)
    monkeypatch.setattr(
        tasks, "_sov_measurement_mode_from_operation_run", lambda *_args: measurement_mode
    )
    monkeypatch.setattr(tasks, "hospital_in_monthly_cohort", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tasks, "tracking_set_members", lambda targets: targets)
    monkeypatch.setattr(
        tasks,
        "_build_measurement_specs",
        lambda **_kwargs: (
            [
                {
                    "query_id": uuid.uuid4(),
                    "query_text": "강남 병원 추천",
                    "platform": "CHATGPT",
                    "target_id": uuid.uuid4(),
                    "variant_id": uuid.uuid4(),
                    "manifest_cell": SimpleNamespace(state="FAILED"),
                }
            ],
            0,
        ),
    )
    monkeypatch.setattr(
        tasks,
        "_record_weekly_sov_failure",
        lambda *args, **kwargs: recorded.append({"args": args, "kwargs": kwargs}),
    )
    monkeypatch.setattr(
        tasks,
        "_finish_sov_operation_run",
        lambda *args, **kwargs: finished.append({"args": args, "kwargs": kwargs}) or uuid.uuid4(),
    )
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(2026, 8, 31, tzinfo="Asia/Seoul"),
    )
    return task, retries, finished, recorded


def test_cost_guard_block_closes_failed_run_without_retry(monkeypatch):
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="테스트의원",
        status=HospitalStatus.ACTIVE,
        competitors=[],
        region=["서울"],
    )
    task, retries, finished, recorded = _patch_sov_task_shell(monkeypatch, hospital)

    def boom(*_args, **_kwargs):
        raise ManifestError("no freeze")

    monkeypatch.setattr(tasks, "freeze_dispatch_manifest", boom)

    _call_run_sov(task, str(hospital.id))

    assert retries == []
    assert recorded[0]["args"][2].endswith("NO_MEASUREMENT_MANIFEST")
    assert finished[0]["args"][2] == OperationRunState.FAILED


def test_policy_drift_closes_failed_run_without_retry(monkeypatch):
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="테스트의원",
        status=HospitalStatus.ACTIVE,
        competitors=[],
        region=["서울"],
    )
    task, retries, finished, recorded = _patch_sov_task_shell(monkeypatch, hospital)
    monkeypatch.setattr(
        tasks,
        "freeze_dispatch_manifest",
        lambda *_args, **_kwargs: SimpleNamespace(cells=[SimpleNamespace(state="FAILED")]),
    )
    monkeypatch.setattr(
        tasks,
        "_pending_weekly_manifest_specs",
        lambda manifest, specs: specs,
    )
    monkeypatch.setattr(tasks, "_manifest_execution_policy_matches", lambda *_args: False)

    _call_run_sov(task, str(hospital.id))

    assert retries == []
    assert recorded[0]["args"][2].endswith("MEASUREMENT_POLICY_DRIFT")
    assert finished[0]["args"][2] == OperationRunState.FAILED


def test_cost_guard_deny_during_spec_loop_does_not_retry(monkeypatch):
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="테스트의원",
        status=HospitalStatus.ACTIVE,
        competitors=[],
        region=["서울"],
    )
    task, retries, finished, recorded = _patch_sov_task_shell(monkeypatch, hospital)
    cell = SimpleNamespace(state="FAILED")
    spec = {
        "query_id": uuid.uuid4(),
        "query_text": "강남 병원 추천",
        "platform": "CHATGPT",
        "target_id": uuid.uuid4(),
        "variant_id": uuid.uuid4(),
        "manifest_cell": cell,
    }
    monkeypatch.setattr(
        tasks,
        "freeze_dispatch_manifest",
        lambda *_args, **_kwargs: SimpleNamespace(cells=[cell]),
    )
    monkeypatch.setattr(tasks, "_pending_weekly_manifest_specs", lambda manifest, specs: [spec])
    monkeypatch.setattr(tasks, "_manifest_execution_policy_matches", lambda *_args: True)
    monkeypatch.setattr(tasks, "_start_measurement_run", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(tasks, "_finish_measurement_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "_sov_chunk_deadline_reached", lambda *_args: False)

    async def blocked(*_args, **_kwargs):
        return SimpleNamespace(allowed=False, reason="월간 호출 상한")

    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", blocked)
    provider_calls = []
    monkeypatch.setattr(
        tasks,
        "run_single_query",
        lambda *_args, **_kwargs: provider_calls.append(True) or [],
    )

    _call_run_sov(task, str(hospital.id))

    assert retries == []
    assert provider_calls == []
    assert recorded[0]["args"][2].endswith("COST_GUARD_BLOCKED")
    assert finished[0]["args"][2] == OperationRunState.FAILED


# ── 2. no second Slack card / no 복구 확인 ───────────────────────────────────


def test_run_sov_typed_failure_skips_generic_task_failed_slack(monkeypatch):
    run_id = uuid.uuid4()
    task = SimpleNamespace(request=SimpleNamespace(headers={"operation_run_id": str(run_id)}))
    run = SimpleNamespace(
        id=run_id,
        operation_type="RUN_SOV",
        safe_error_code="MONTHLY_SOV_COST_GUARD_BLOCKED",
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", FakeSession)
    monkeypatch.setattr(task_incident_control, "_tracked_run", lambda *_args: run)
    monkeypatch.setattr(
        task_incident_control,
        "_open_incident",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("generic BACKGROUND_TASK_FAILED must not open")
        ),
    )

    assert task_incident_control.record_task_failure(task, "worker-task") is False


def test_run_sov_success_recovers_generic_incident_without_slack(monkeypatch):
    run_id = uuid.uuid4()
    task = SimpleNamespace(request=SimpleNamespace(headers={"operation_run_id": str(run_id)}))
    run = SimpleNamespace(id=run_id, operation_type="RUN_SOV", task_id="worker-task")
    incident = SimpleNamespace(
        id=uuid.uuid4(),
        state="OPEN",
        version=1,
    )
    enqueued = []

    class FakeSession:
        def scalar(self, _stmt):
            return incident

        def commit(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", FakeSession)
    monkeypatch.setattr(task_incident_control, "_tracked_run", lambda *_args: run)
    monkeypatch.setattr(
        task_incident_control,
        "_transition_incident",
        lambda *_args, **kwargs: SimpleNamespace(
            id=incident.id,
            state=kwargs["next_state"].value,
            version=incident.version + 1,
        ),
    )
    monkeypatch.setattr(
        task_incident_control, "_enqueue", lambda *_args: enqueued.append(True)
    )
    monkeypatch.setattr(task_incident_control, "_audit", lambda *_args: None)

    assert task_incident_control.record_task_success(task, "worker-task") is True
    assert enqueued == []


# ── 3. 6-hour beat does not rearm COST_GUARD when budget does not fit ────────


def _failed_monthly_run(*, code: str, state=OperationRunState.FAILED):
    return SimpleNamespace(
        state=state,
        task_id=str(uuid.uuid4()),
        queued_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        lease_owner="worker",
        lease_expires_at=datetime.now(UTC),
        success_count=0,
        failure_count=1,
        skipped_count=0,
        safe_error_code=code,
        safe_error_message="failed",
        version=3,
    )


def test_cost_guard_failed_run_does_not_rearm_when_budget_insufficient(monkeypatch):
    existing = _failed_monthly_run(code="MONTHLY_SOV_COST_GUARD_BLOCKED")

    class _DB:
        commits = 0

        def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: existing)

        def commit(self):
            self.commits += 1

    monkeypatch.setattr(tasks, "_monthly_sov_pending_budget_fits", lambda *_args: False)

    run = tasks._ensure_monthly_sov_operation_run(
        _DB(), SimpleNamespace(id=uuid.uuid4()), "2026-08", datetime.now(UTC)
    )

    assert run is None
    assert existing.state == OperationRunState.FAILED
    assert existing.version == 3


def test_cost_guard_failed_run_rearms_when_remaining_units_cover_pending(monkeypatch):
    existing = _failed_monthly_run(code="MONTHLY_SOV_COST_GUARD_BLOCKED")

    class _DB:
        commits = 0

        def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: existing)

        def commit(self):
            self.commits += 1

    monkeypatch.setattr(tasks, "_monthly_sov_pending_budget_fits", lambda *_args: True)

    run = tasks._ensure_monthly_sov_operation_run(
        _DB(), SimpleNamespace(id=uuid.uuid4()), "2026-08", datetime.now(UTC)
    )

    assert run is existing
    assert run.state == OperationRunState.REQUESTED
    assert run.version == 4


def test_pending_budget_fit_counts_failed_cells_times_repeat(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4())
    manifest = SimpleNamespace(
        cells=[
            SimpleNamespace(state="FAILED"),
            SimpleNamespace(state="FAILED"),
            SimpleNamespace(state="SUCCESS"),
        ]
    )

    class _DB:
        def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: manifest)

    async def remaining(_category):
        return (20, 20)

    monkeypatch.setattr(tasks.cost_guard, "remaining_units", remaining)
    assert tasks._monthly_sov_pending_budget_fits(_DB(), hospital, "2026-08") is True

    async def too_small(_category):
        return (5, 100)

    monkeypatch.setattr(tasks.cost_guard, "remaining_units", too_small)
    assert tasks._monthly_sov_pending_budget_fits(_DB(), hospital, "2026-08") is False


# ── 4. per-spec reserve + chunk-commit ───────────────────────────────────────


def test_measurement_reserves_one_spec_at_a_time_and_chunk_commits():
    source = inspect.getsource(tasks.run_sov_for_hospital)
    assert "reserved_units = SOV_REPEAT_WEEKLY" in source
    assert "count=reserved_units" in source
    assert "query_count=len(measurement_specs)" not in source
    assert "db.commit()" in source
    assert "_sov_chunk_deadline_reached" in source
    assert "release_reservation" in source


def test_monthly_15x2x5_at_concurrency_2_exceeds_1800s_so_chunk_stop_exists():
    calls = 15 * 2 * 5
    waves = -(-calls // 2)
    estimated = waves * 62.8
    assert estimated > 1800
    assert tasks.SOV_CHUNK_STOP_SECONDS < 1800
    assert tasks.run_sov_for_hospital.soft_time_limit == 1800


# ── 5. weekly must not drain month-end Redis ─────────────────────────────────


def test_weekly_skips_remaining_hospital_dispatch_during_month_end_window(monkeypatch):
    remaining = SimpleNamespace(id=uuid.uuid4(), status=HospitalStatus.ACTIVE)
    dispatched = []

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [remaining]

    class _DB:
        def execute(self, _stmt):
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks, "register_convertible_tracking_sets", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(tasks, "iter_monthly_sov_cohort", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(2026, 8, 31, 2, tzinfo="Asia/Seoul"),
    )
    monkeypatch.setattr(
        tasks.run_sov_for_hospital,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs),
    )

    tasks.run_weekly_monitoring.run()

    assert dispatched == []


def test_weekly_still_dispatches_non_cohort_before_window(monkeypatch):
    remaining = SimpleNamespace(id=uuid.uuid4(), status=HospitalStatus.ACTIVE)
    dispatched = []

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [remaining]

        def scalar_one_or_none(self):
            return None

        def returning(self, *_args):
            return self

    class _DB:
        def __init__(self):
            self.added = []

        def execute(self, stmt):
            compiled = str(stmt)
            if "operation_runs" in compiled.lower() or "OperationRun" in type(stmt).__name__:
                return _Result()
            return _Result()

        def add(self, value):
            self.added.append(value)

        def commit(self):
            return None

        def rollback(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks, "register_convertible_tracking_sets", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(tasks, "iter_monthly_sov_cohort", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(2026, 8, 10, 2, tzinfo="Asia/Seoul"),
    )
    monkeypatch.setattr(
        tasks,
        "_ensure_weekly_sov_operation_run",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4(), task_id=str(uuid.uuid4())),
    )
    monkeypatch.setattr(
        tasks.run_sov_for_hospital,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs),
    )
    monkeypatch.setattr(tasks, "_mark_weekly_sov_operation_queued", lambda *_args: True)
    monkeypatch.setattr(
        tasks.adjust_query_priorities, "apply_async", lambda **_kwargs: None
    )

    tasks.run_weekly_monitoring.run()

    assert len(dispatched) == 1


# ── 6. per-hospital COST_GUARD Slack is forbidden ────────────────────────────


@pytest.mark.asyncio
async def test_cost_guard_blocked_opens_incident_without_slack(monkeypatch):
    enqueued = []
    opened = []

    class _DB:
        async def scalar(self, _stmt):
            return None

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    class _Sessions:
        def __call__(self):
            return _DB()

    async def fake_open(db, request, **_kwargs):
        opened.append(request)
        return SimpleNamespace(
            id=uuid.uuid4(),
            severity="HIGH",
            customer_impact=request.customer_impact,
            next_action=request.next_action,
            admin_path=request.admin_path,
            hospital_id=request.hospital_id,
            operation_run_id=request.operation_run_id,
            version=1,
            episode_seq=1,
            safe_error_message=request.safe_error_message,
        )

    async def fake_enqueue(*_args, **_kwargs):
        enqueued.append(True)

    monkeypatch.setattr(
        weekly_sov_incident_control, "get_async_sessionmaker", lambda: _Sessions()
    )
    monkeypatch.setattr(weekly_sov_incident_control, "open_or_touch_incident", fake_open)
    monkeypatch.setattr(weekly_sov_incident_control, "enqueue_notification", fake_enqueue)

    incident_id = await weekly_sov_incident_control.open_monthly_sov_failure(
        hospital_id=uuid.uuid4(),
        hospital_name="테스트의원",
        period_key="2026-08",
        error_code="MONTHLY_SOV_COST_GUARD_BLOCKED",
        operation_run_id=uuid.uuid4(),
    )

    assert incident_id is not None
    assert opened and opened[0].safe_error_code == "MONTHLY_SOV_COST_GUARD_BLOCKED"
    assert enqueued == []


@pytest.mark.asyncio
async def test_human_action_measurement_failure_still_slacks(monkeypatch):
    enqueued = []

    class _DB:
        async def scalar(self, _stmt):
            return None

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    class _Sessions:
        def __call__(self):
            return _DB()

    async def fake_open(db, request, **_kwargs):
        return SimpleNamespace(
            id=uuid.uuid4(),
            severity="HIGH",
            customer_impact=request.customer_impact,
            next_action=request.next_action,
            admin_path=request.admin_path,
            hospital_id=request.hospital_id,
            operation_run_id=request.operation_run_id,
            version=1,
            episode_seq=1,
            safe_error_message=request.safe_error_message,
        )

    async def fake_enqueue(*_args, **_kwargs):
        enqueued.append(True)

    monkeypatch.setattr(
        weekly_sov_incident_control, "get_async_sessionmaker", lambda: _Sessions()
    )
    monkeypatch.setattr(weekly_sov_incident_control, "open_or_touch_incident", fake_open)
    monkeypatch.setattr(weekly_sov_incident_control, "enqueue_notification", fake_enqueue)

    await weekly_sov_incident_control.open_monthly_sov_failure(
        hospital_id=uuid.uuid4(),
        hospital_name="테스트의원",
        period_key="2026-08",
        error_code="MONTHLY_SOV_NO_MEASUREMENT_MANIFEST",
    )

    assert enqueued == [True]


def test_no_slack_digest_helper_was_added_as_the_fix():
    source = Path(weekly_sov_incident_control.__file__).read_text(encoding="utf-8")
    assert "digest" not in inspect.getsource(weekly_sov_incident_control._open_sov_failure)
    assert "COST_GUARD_BLOCKED" in source


# ── additional STOP: 9/1 00:15 August report requires monthly SUCCESS ─────────


def _patch_monthly_report_batch(monkeypatch, hospitals, *, now, succeeded_ids=None):
    succeeded_ids = set(succeeded_ids or ())
    built: list[tuple[object, int, int]] = []
    run_id = uuid.uuid4()

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return list(hospitals)

        def scalar_one_or_none(self):
            return None

    class _DB:
        def execute(self, _stmt):
            return _Result()

        def get(self, _model, item_id):
            if item_id == run_id:
                return SimpleNamespace(state=OperationRunState.SUCCEEDED)
            return next((h for h in hospitals if h.id == item_id), None)

        def rollback(self):
            pytest.fail("monthly report batch rolled back")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks, "eligible_hospital_ids", lambda *_args: [hospital.id for hospital in hospitals]
    )
    monkeypatch.setattr(
        tasks,
        "_monthly_sov_measurement_succeeded",
        lambda _db, hospital_id, _period_key: hospital_id in succeeded_ids,
    )
    monkeypatch.setattr(
        tasks, "_start_scheduled_monthly_operation_run", lambda *_args: (run_id, False)
    )
    monkeypatch.setattr(tasks, "_latest_monthly_report", lambda *_args: None)

    def _build(_db, observed_hospital, anchor, **_kwargs):
        built.append((observed_hospital.id, anchor.year, anchor.month))
        return "created"

    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", _build)
    monkeypatch.setattr(tasks, "_finish_monthly_operation_run", lambda *_args: None)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(now),
    )
    return built


def test_sep1_close_is_august_and_conversion_window_is_off():
    now = tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul").datetime
    period = scheduled_report_period(now)
    assert (period.year, period.month) == (2026, 8)
    assert is_august_2026_conversion_window(now) is False


def test_sep1_does_not_build_august_report_without_monthly_success(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="행복드림의원")
    built = _patch_monthly_report_batch(
        monkeypatch,
        [hospital],
        now=tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul"),
        succeeded_ids=set(),
    )

    result = tasks.run_monthly_reports.run()

    assert built == []
    assert result == {
        "status": "SUCCEEDED",
        "total_count": 0,
        "success_count": 0,
        "failure_count": 0,
    }


def test_sep1_succeeded_converted_hospital_can_build_august_report(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="장편한외과")
    built = _patch_monthly_report_batch(
        monkeypatch,
        [hospital],
        now=tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul"),
        succeeded_ids={hospital.id},
    )

    result = tasks.run_monthly_reports.run()

    assert built == [(hospital.id, 2026, 8)]
    assert result["status"] == "SUCCEEDED"


def test_sep1_does_not_overwrite_july_when_building_august(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="연세속시원내과")
    july = SimpleNamespace(id=uuid.uuid4(), version=2, pdf_path="gs://reports/july.pdf")
    snapshot = (july.id, july.version, july.pdf_path)
    built = _patch_monthly_report_batch(
        monkeypatch,
        [hospital],
        now=tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul"),
        succeeded_ids={hospital.id},
    )

    def _latest(_db, hospital_id, year, month):
        assert hospital_id == hospital.id
        assert (year, month) == (2026, 8)
        return None

    monkeypatch.setattr(tasks, "_latest_monthly_report", _latest)

    tasks.run_monthly_reports.run()

    assert built == [(hospital.id, 2026, 8)]
    assert (july.id, july.version, july.pdf_path) == snapshot


def test_sep1_conversion_window_off_does_not_unbind_success_gate(monkeypatch):
    now = tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul").datetime
    assert is_august_2026_conversion_window(now) is False
    failed = SimpleNamespace(id=uuid.uuid4(), name="강심장내과")
    built = _patch_monthly_report_batch(
        monkeypatch,
        [failed],
        now=tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul"),
        succeeded_ids=set(),
    )

    tasks.run_monthly_reports.run()

    assert built == []
    source = inspect.getsource(tasks.run_monthly_reports)
    assert "is_august_2026_conversion_window" not in source
    assert "_hospital_requires_monthly_sov_success" in source


def test_sep1_non_converted_hospital_without_monthly_run_still_builds(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="주간측정외과")
    assert not any(token in hospital.name for token in CONVERSION_HOSPITAL_NAME_TOKENS)
    built = _patch_monthly_report_batch(
        monkeypatch,
        [hospital],
        now=tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul"),
        succeeded_ids=set(),
    )

    result = tasks.run_monthly_reports.run()

    assert built == [(hospital.id, 2026, 8)]
    assert result["status"] == "SUCCEEDED"


def test_generate_monthly_report_skips_converted_hospital_without_success(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="마포성모탑의원")

    class _DB:
        def get(self, _model, _id):
            return hospital

        def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(
        tasks, "_monthly_sov_measurement_succeeded", lambda *_args: False
    )
    monkeypatch.setattr(
        tasks,
        "_build_monthly_report_for_hospital",
        lambda *_args, **_kwargs: pytest.fail("manual August report built without success"),
    )
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul"),
    )

    result = tasks.generate_monthly_report_for_hospital.run(str(hospital.id))

    assert result == {
        "status": "measurement_not_succeeded",
        "year": 2026,
        "month": 8,
    }


def test_monthly_measurement_beat_stays_day_24_to_31_and_skips_sep1(monkeypatch):
    entry = celery_app.conf.beat_schedule["monthly-sov-measurement"]
    assert entry["schedule"].day_of_month == set(range(24, 32))
    dispatched = []
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(2026, 9, 1, 0, 0, tzinfo="Asia/Seoul"),
    )
    monkeypatch.setattr(
        tasks.run_sov_for_hospital,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs),
    )

    tasks.run_monthly_sov_measurement.run()

    assert dispatched == []
