"""Pin the 2026-08-31 month-end visibility-measurement repeat-alert cut."""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
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
    """OPEN 공지가 나간 적이 없으면 복구도 조용히 끝난다.

    RUN_SOV의 비용 차단·분류된 실패는 파이프라인이 자기 인시던트를 내므로 위
    `record_task_failure`가 일찍 반환하고 generic OPEN 공지를 만들지 않는다. 그 뒤의
    성공은 인시던트만 닫고 Slack을 만들지 않아야 한다. 반대로 OPEN이 실제로 나간
    건이라면 RECOVERED가 반드시 따라간다(tests/test_task_incidents.py).
    """
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
        def __init__(self):
            # 조회 순서: 인시던트 → 이 인시던트의 INCIDENT_OPEN outbox 행(없음)
            self._results = [incident, None]

        def scalar(self, _stmt):
            return self._results.pop(0) if self._results else None

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


# ── 3. bounded windows rearm failed monthly cells ─────────────────────────────


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


def test_partial_monthly_run_rearms_for_failed_cell_retry():
    existing = _failed_monthly_run(
        code="MONTHLY_SOV_MEASUREMENT_PARTIAL",
        state=OperationRunState.PARTIAL,
    )
    old_task_id = existing.task_id

    class _DB:
        commits = 0

        def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: existing)

        def commit(self):
            self.commits += 1

    db = _DB()
    run = tasks._ensure_monthly_sov_operation_run(
        db, SimpleNamespace(id=uuid.uuid4()), "2026-08", datetime.now(UTC)
    )

    assert run is existing
    assert run.state == OperationRunState.REQUESTED
    assert run.task_id != old_task_id
    assert run.version == 4
    assert db.commits == 1


@pytest.mark.parametrize(
    "code",
    ["MONTHLY_SOV_MEASUREMENT_POLICY_DRIFT", "MONTHLY_SOV_MEASUREMENT_PARTIAL"],
)
def test_non_cost_failed_monthly_run_rearms_failed_cells_in_month_end_window(code):
    existing = _failed_monthly_run(code=code)
    manifest = SimpleNamespace(cells=[SimpleNamespace(state="FAILED")])

    class _DB:
        commits = 0

        def __init__(self):
            self.results = iter((existing, manifest))

        def execute(self, _stmt):
            value = next(self.results)
            return SimpleNamespace(scalar_one_or_none=lambda: value)

        def commit(self):
            self.commits += 1

    db = _DB()
    run = tasks._ensure_monthly_sov_operation_run(
        db,
        SimpleNamespace(id=uuid.uuid4()),
        "2026-08",
        datetime(2026, 8, 28, tzinfo=UTC),
    )

    assert run is existing
    assert existing.state == OperationRunState.REQUESTED
    assert existing.version == 4
    assert db.commits == 1


def test_non_cost_failed_monthly_run_stays_closed_outside_bounded_windows():
    existing = _failed_monthly_run(code="MONTHLY_SOV_MEASUREMENT_PARTIAL")

    class _DB:
        commits = 0

        def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: existing)

        def commit(self):
            self.commits += 1

    db = _DB()
    run = tasks._ensure_monthly_sov_operation_run(
        db,
        SimpleNamespace(id=uuid.uuid4()),
        "2026-08",
        datetime(2026, 9, 8, tzinfo=UTC),
    )

    assert run is None
    assert existing.state == OperationRunState.FAILED
    assert db.commits == 0


def test_monthly_task_resolves_prior_period_only_after_close_cutoff():
    before = tasks.arrow.get(2026, 9, 1, 0, 14, 59, tzinfo="Asia/Seoul")
    at_cutoff = tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul")

    assert (
        tasks._resolve_monthly_measurement_period(
            before.date(), 2026, 8, observed_at=before.datetime
        )
        is None
    )
    assert tasks._resolve_monthly_measurement_period(
        at_cutoff.date(), 2026, 8, observed_at=at_cutoff.datetime
    ) == (2026, 8)


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


def test_weekly_skips_only_monthly_cohort_during_month_end_window(monkeypatch):
    """월말 창(24일+)에는 월간 코호트에 든 병원만 주간 배치에서 빠진다 — 코호트 밖
    병원까지 통째로 스킵되던 것은 2026-09-01 무음실패 리뷰 §2.4-1의 확인된 버그였다."""

    cohort = SimpleNamespace(id=uuid.uuid4(), status=HospitalStatus.ACTIVE)
    remaining = SimpleNamespace(id=uuid.uuid4(), status=HospitalStatus.ACTIVE)
    dispatched = []

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [cohort, remaining]

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
    monkeypatch.setattr(tasks, "iter_monthly_sov_cohort", lambda *_args, **_kwargs: [cohort])
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(2026, 8, 31, 2, tzinfo="Asia/Seoul"),
    )
    monkeypatch.setattr(
        tasks,
        "_ensure_weekly_sov_operation_run",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4(), task_id=str(uuid.uuid4())),
    )
    monkeypatch.setattr(
        tasks.run_sov_for_hospital,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs["args"][0]),
    )
    monkeypatch.setattr(tasks, "_mark_weekly_sov_operation_queued", lambda *_args: True)
    monkeypatch.setattr(tasks.adjust_query_priorities, "apply_async", lambda **_kwargs: None)

    tasks.run_weekly_monitoring.run()

    assert dispatched == [str(remaining.id)]
    assert str(cohort.id) not in dispatched


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
    monkeypatch.setattr(tasks, "_latest_monthly_report_operation_run", lambda *_args: None)
    monkeypatch.setattr(tasks, "_latest_monthly_report", lambda *_args: None)

    def _build(_db, observed_hospital, anchor, **_kwargs):
        built.append((observed_hospital.id, anchor.year, anchor.month))
        return "created"

    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", _build)
    monkeypatch.setattr(tasks, "_finish_monthly_operation_run", lambda *_args: None)
    monkeypatch.setattr(tasks, "_dispatch_monthly_sov_catchup", lambda *_args: None)
    monkeypatch.setattr(tasks, "_record_weekly_sov_failure", lambda *_args, **_kwargs: None)
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
    hospital = SimpleNamespace(id=uuid.uuid4(), name="행복드림의원", monthly_sov_cohort=True)
    built = _patch_monthly_report_batch(
        monkeypatch,
        [hospital],
        now=tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul"),
        succeeded_ids=set(),
    )

    catchups = []
    incidents = []
    monkeypatch.setattr(
        tasks,
        "_dispatch_monthly_sov_catchup",
        lambda *_args: catchups.append(True) or uuid.uuid4(),
    )
    monkeypatch.setattr(
        tasks,
        "_record_weekly_sov_failure",
        lambda *args, **kwargs: incidents.append((args, kwargs)),
    )

    result = tasks.run_monthly_reports.run()

    assert built == []
    assert result == {
        "status": "PARTIAL",
        "total_count": 1,
        "success_count": 0,
        "failure_count": 1,
    }
    assert catchups == [True]
    assert len(incidents) == 1
    assert incidents[0][0][2] == "MONTHLY_SOV_MEASUREMENT_INCOMPLETE"
    assert incidents[0][1]["measurement_mode"] == "monthly"


def test_sep1_succeeded_converted_hospital_can_build_august_report(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="장편한외과", monthly_sov_cohort=True)
    built = _patch_monthly_report_batch(
        monkeypatch,
        [hospital],
        now=tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul"),
        succeeded_ids={hospital.id},
    )

    result = tasks.run_monthly_reports.run()

    assert built == [(hospital.id, 2026, 8)]
    assert result["status"] == "SUCCEEDED"


@pytest.mark.parametrize(
    ("run_state", "expected_status"),
    [
        (OperationRunState.SUCCEEDED, "SUCCEEDED"),
        (OperationRunState.PARTIAL, "PARTIAL"),
    ],
)
def test_daily_close_does_not_duplicate_terminal_report_run(
    monkeypatch, run_state, expected_status
):
    hospital = SimpleNamespace(
        id=uuid.uuid4(), name="자동복구의원", monthly_sov_cohort=True
    )
    built = _patch_monthly_report_batch(
        monkeypatch,
        [hospital],
        now=tasks.arrow.get(2026, 9, 2, 0, 15, tzinfo="Asia/Seoul"),
        succeeded_ids={hospital.id},
    )
    monkeypatch.setattr(
        tasks,
        "_latest_monthly_report_operation_run",
        lambda *_args: SimpleNamespace(state=run_state),
    )

    result = tasks.run_monthly_reports.run()

    assert built == []
    assert result["status"] == expected_status
    assert result["success_count"] == (
        1 if run_state == OperationRunState.SUCCEEDED else 0
    )


def test_latest_report_run_resolution_uses_the_requested_period():
    hospital_id = uuid.uuid4()
    other = SimpleNamespace(
        result_summary={"period_year": 2026, "period_month": 7},
        request_payload={},
        idempotency_key=f"scheduled:{hospital_id}:2026-07",
    )
    requested = SimpleNamespace(
        result_summary={"period_year": 2026, "period_month": 8},
        request_payload={},
        idempotency_key=f"coverage-recovery:{hospital_id}:2026-08",
    )

    class _DB:
        def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [other, requested])
            )

    assert (
        tasks._latest_monthly_report_operation_run(_DB(), hospital_id, 2026, 8)
        is requested
    )


def _coverage_recovery_db(existing):
    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class _DB:
        def __init__(self):
            self.commits = 0
            self.added = []
            self._existing = existing

        def execute(self, _statement):
            return _Result(self._existing)

        def add(self, obj):
            self.added.append(obj)

        def commit(self):
            self.commits += 1

    return _DB()


def test_failed_coverage_recovery_rearms_after_complete_remeasure(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="복구재시도의원")
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.FAILED,
        idempotency_key=f"coverage-recovery:{hospital.id}:2026-08",
        task_id="old-task",
        queued_at=datetime(2026, 9, 2, tzinfo=UTC),
        started_at=datetime(2026, 9, 2, tzinfo=UTC),
        completed_at=datetime(2026, 9, 2, tzinfo=UTC),
        heartbeat_at=None,
        lease_owner="dead-worker",
        lease_expires_at=datetime(2026, 9, 2, tzinfo=UTC),
        success_count=0,
        failure_count=1,
        skipped_count=0,
        safe_error_code="MONTHLY_REPORT_FAILED",
        safe_error_message="이전 자동 복구가 실패했습니다.",
        request_payload={},
        result_summary={"period_year": 2026, "period_month": 8},
        attempt_count=1,
        version=3,
    )
    # Remeasure is done; monthly report itself is still incomplete → must rearm.
    report = SimpleNamespace(
        quality="PARTIAL",
        planned_count=10,
        success_count=4,
        failed_count=6,
        excluded_count=0,
    )
    db = _coverage_recovery_db(existing)
    dispatches = []

    monkeypatch.setattr(tasks, "_latest_monthly_report", lambda *_a, **_k: report)
    monkeypatch.setattr(
        tasks.generate_monthly_report_for_hospital,
        "apply_async",
        lambda **kwargs: dispatches.append(kwargs),
    )
    monkeypatch.setattr(
        tasks, "build_dispatch_headers", lambda *_a, **_k: {"purpose": "generate-monthly-report"}
    )
    monkeypatch.setattr(tasks, "_mark_weekly_sov_operation_queued", lambda *_a, **_k: None)

    run = tasks._dispatch_automatic_monthly_report_recovery(db, hospital, 2026, 8)

    assert run is existing
    assert existing.state == OperationRunState.REQUESTED
    assert existing.task_id != "old-task"
    assert existing.safe_error_code is None
    assert existing.safe_error_message is None
    assert existing.version == 4
    assert existing.attempt_count == 1
    assert db.commits == 1
    assert db.added == []
    assert len(dispatches) == 1
    assert dispatches[0]["task_id"] == existing.task_id
    assert dispatches[0]["args"] == [str(hospital.id), 2026, 8, True, True]
    assert dispatches[0]["headers"]["operation_run_id"] == str(existing.id)


def test_complete_report_skips_coverage_recovery_redispatch(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="완료의원")
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        state=OperationRunState.FAILED,
        version=2,
        task_id="should-not-change",
    )
    report = SimpleNamespace(
        quality="COMPLETE",
        planned_count=10,
        success_count=10,
        failed_count=0,
        excluded_count=0,
    )
    db = _coverage_recovery_db(existing)
    dispatches = []
    monkeypatch.setattr(tasks, "_latest_monthly_report", lambda *_a, **_k: report)
    monkeypatch.setattr(
        tasks.generate_monthly_report_for_hospital,
        "apply_async",
        lambda **kwargs: dispatches.append(kwargs),
    )

    assert tasks._dispatch_automatic_monthly_report_recovery(db, hospital, 2026, 8) is None
    assert dispatches == []
    assert existing.state == OperationRunState.FAILED
    assert existing.task_id == "should-not-change"


def test_succeeded_complete_coverage_recovery_does_not_redispatch(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="성공완료의원")
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        state=OperationRunState.SUCCEEDED,
        version=5,
        task_id="done-task",
        attempt_count=1,
    )
    report = SimpleNamespace(
        quality="COMPLETE",
        planned_count=8,
        success_count=8,
        failed_count=0,
        excluded_count=0,
    )
    db = _coverage_recovery_db(existing)
    dispatches = []
    monkeypatch.setattr(tasks, "_latest_monthly_report", lambda *_a, **_k: report)
    monkeypatch.setattr(
        tasks.generate_monthly_report_for_hospital,
        "apply_async",
        lambda **kwargs: dispatches.append(kwargs),
    )

    assert tasks._dispatch_automatic_monthly_report_recovery(db, hospital, 2026, 8) is None
    assert dispatches == []
    assert existing.state == OperationRunState.SUCCEEDED
    assert existing.task_id == "done-task"
    assert existing.version == 5


@pytest.mark.parametrize(
    "state",
    [
        OperationRunState.REQUESTED,
        OperationRunState.QUEUED,
        OperationRunState.RUNNING,
    ],
)
def test_fresh_coverage_recovery_run_does_not_redispatch(monkeypatch, state):
    now = datetime.now(UTC)
    hospital = SimpleNamespace(id=uuid.uuid4(), name="진행중의원")
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        state=state,
        task_id="in-flight-task",
        requested_at=now,
        queued_at=now if state != OperationRunState.REQUESTED else None,
        started_at=now if state == OperationRunState.RUNNING else None,
        heartbeat_at=now if state == OperationRunState.RUNNING else None,
        lease_owner="worker" if state == OperationRunState.RUNNING else None,
        lease_expires_at=(
            now + timedelta(minutes=5) if state == OperationRunState.RUNNING else None
        ),
    )
    db = _coverage_recovery_db(existing)
    monkeypatch.setattr(tasks, "_latest_monthly_report", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tasks.generate_monthly_report_for_hospital,
        "apply_async",
        lambda **_kwargs: pytest.fail("fresh in-flight run was redispatched"),
    )

    run = tasks._dispatch_automatic_monthly_report_recovery(db, hospital, 2026, 8)

    assert run is existing
    assert existing.task_id == "in-flight-task"
    assert db.commits == 0


def test_task_success_without_complete_manifest_is_reconciled_to_partial():
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        state=OperationRunState.SUCCEEDED,
        total_count=1,
        success_count=1,
        failure_count=0,
        safe_error_code=None,
        safe_error_message=None,
        result_summary={"measurement_mode": "monthly"},
        version=4,
    )
    cells = [
        SimpleNamespace(state="FAILED", platform="chatgpt") for _ in range(105)
    ] + [SimpleNamespace(state="FAILED", platform="gemini") for _ in range(105)]
    manifest = SimpleNamespace(
        cells=cells,
        configured_platforms=["chatgpt", "gemini"],
        closes_at=datetime(2026, 9, 1, 0, 15, tzinfo=UTC),
        closed_at=None,
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            return self

        def first(self):
            return self.value

    class _DB:
        def __init__(self):
            self.results = iter((run, manifest))
            self.commits = 0

        def execute(self, _statement):
            return _Result(next(self.results))

        def commit(self):
            self.commits += 1

    db = _DB()

    assert not tasks._monthly_sov_measurement_succeeded(db, hospital_id, "2026-08")
    assert manifest.closed_at is None
    assert run.state == OperationRunState.PARTIAL
    assert run.safe_error_code == "MONTHLY_MEASUREMENT_INCOMPLETE"
    assert run.result_summary == {
        "measurement_mode": "monthly",
        "measurement_quality": "INCOMPLETE",
        "planned_count": 210,
        "success_count": 0,
        "failed_count": 210,
        "manifest_closed": False,
    }


def test_task_success_with_closed_complete_manifest_can_build_report():
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(state=OperationRunState.SUCCEEDED)
    manifest = SimpleNamespace(
        cells=[
            SimpleNamespace(state="SUCCESS", platform="chatgpt"),
            SimpleNamespace(state="SUCCESS", platform="gemini"),
        ],
        configured_platforms=["chatgpt", "gemini"],
        closes_at=datetime(2026, 9, 1, 0, 15, tzinfo=UTC),
        closed_at=datetime(2026, 9, 1, 0, 15, tzinfo=UTC),
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            return self

        def first(self):
            return self.value

    class _DB:
        def __init__(self):
            self.results = iter((run, manifest))

        def execute(self, _statement):
            return _Result(next(self.results))

    assert tasks._monthly_sov_measurement_succeeded(_DB(), hospital_id, "2026-08")


def test_partial_scheduled_report_run_is_terminal_not_six_hour_retry():
    hospital = SimpleNamespace(id=uuid.uuid4())
    partial = SimpleNamespace(
        id=uuid.uuid4(),
        state=OperationRunState.PARTIAL,
        heartbeat_at=None,
        started_at=datetime.now(UTC),
        requested_at=datetime.now(UTC),
    )

    class _DB:
        def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: partial)

        def commit(self):
            pytest.fail("terminal PARTIAL report run was reopened")

    run_id, replayed = tasks._start_scheduled_monthly_operation_run(
        _DB(), hospital, tasks.arrow.get(2026, 8, 31, 23, 59, tzinfo="Asia/Seoul")
    )

    assert (run_id, replayed) == (partial.id, True)
    assert partial.state == OperationRunState.PARTIAL


def test_scheduled_batch_does_not_rebuild_existing_degraded_report(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="불완전 측정 의원")
    run_id = uuid.uuid4()
    run = SimpleNamespace(state=OperationRunState.RUNNING)
    degraded = SimpleNamespace(quality="DEGRADED")

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [hospital]

    class _DB:
        def execute(self, _statement):
            return _Result()

        def get(self, _model, item_id):
            return run if item_id == run_id else hospital

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "eligible_hospital_ids", lambda *_args: [hospital.id])
    monkeypatch.setattr(tasks, "_hospital_requires_monthly_sov_success", lambda *_args: False)
    monkeypatch.setattr(
        tasks, "_start_scheduled_monthly_operation_run", lambda *_args: (run_id, False)
    )
    monkeypatch.setattr(tasks, "_latest_monthly_report_operation_run", lambda *_args: None)
    monkeypatch.setattr(tasks, "_latest_monthly_report", lambda *_args: degraded)
    monkeypatch.setattr(
        tasks,
        "_build_monthly_report_for_hospital",
        lambda *_args, **_kwargs: pytest.fail("DEGRADED report was rebuilt"),
    )

    def _finish(_db, _run_id, _hospital_id, _year, _month, outcome):
        assert outcome == "coverage_incomplete"
        run.state = OperationRunState.PARTIAL

    monkeypatch.setattr(tasks, "_finish_monthly_operation_run", _finish)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(2026, 9, 1, 0, 15, tzinfo="Asia/Seoul"),
    )

    assert tasks.run_monthly_reports.run() == {
        "status": "PARTIAL",
        "total_count": 1,
        "success_count": 0,
        "failure_count": 1,
    }


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
    failed = SimpleNamespace(id=uuid.uuid4(), name="강심장내과", monthly_sov_cohort=True)
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
    hospital = SimpleNamespace(
        id=uuid.uuid4(), name="주간측정외과", monthly_sov_cohort=False
    )
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
    run_id = uuid.uuid4()
    run = SimpleNamespace(
        state=OperationRunState.RUNNING,
        completed_at=None,
        heartbeat_at=datetime.now(UTC),
        lease_owner="worker-task",
        lease_expires_at=datetime.now(UTC),
        total_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        safe_error_code=None,
        safe_error_message=None,
        result_summary=None,
        version=2,
    )
    hospital = SimpleNamespace(
        id=uuid.uuid4(), name="마포성모탑의원", monthly_sov_cohort=True
    )

    class _DB:
        def get(self, _model, item_id):
            return run if item_id == run_id else hospital

        def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

        def commit(self):
            return None

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

    def _mark(_db, _run_id, _hospital_id, _year, _month):
        assert _run_id is None
        assert _hospital_id == hospital.id
        run.state = OperationRunState.PARTIAL
        run.safe_error_code = "MONTHLY_MEASUREMENT_INCOMPLETE"

    monkeypatch.setattr(tasks, "_mark_monthly_report_measurement_incomplete", _mark)
    result = tasks.generate_monthly_report_for_hospital.run(str(hospital.id))

    assert result == {
        "skipped": True,
        "status": "measurement_not_succeeded",
        "message": "필수 측정이 완료되지 않아 리포트를 만들지 않았습니다.",
        "year": 2026,
        "month": 8,
    }
    assert run.state == OperationRunState.PARTIAL
    assert run.safe_error_code == "MONTHLY_MEASUREMENT_INCOMPLETE"


def test_measurement_not_succeeded_terminalizer_sets_partial_run_state():
    run_id = uuid.uuid4()
    run = SimpleNamespace(
        state=OperationRunState.RUNNING,
        completed_at=None,
        heartbeat_at=datetime.now(UTC),
        lease_owner="worker",
        lease_expires_at=datetime.now(UTC),
        total_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        safe_error_code=None,
        safe_error_message=None,
        result_summary=None,
        version=2,
    )

    class _DB:
        def get(self, _model, item_id):
            return run if item_id == run_id else None

        def commit(self):
            return None

    tasks._mark_monthly_report_measurement_incomplete(
        _DB(), run_id, uuid.uuid4(), 2026, 8
    )

    assert run.state == OperationRunState.PARTIAL
    assert run.failure_count == 1
    assert run.safe_error_code == "MONTHLY_MEASUREMENT_INCOMPLETE"


def test_complete_monthly_remeasurement_closes_and_dispatches_report(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="자동복구의원")
    manifest = SimpleNamespace(
        cells=[],
        configured_platforms=["chatgpt"],
        closes_at=datetime(2026, 9, 1, tzinfo=UTC),
        closed_at=None,
    )
    commits = []
    dispatched = []
    db = SimpleNamespace(commit=lambda: commits.append(True))
    task = SimpleNamespace(request=SimpleNamespace(headers={}))
    monkeypatch.setattr(
        tasks,
        "summarize_manifest",
        lambda *_args, **_kwargs: SimpleNamespace(
            quality="COMPLETE",
            planned_count=1,
            success_count=1,
            failed_count=0,
            excluded_count=0,
        ),
    )
    monkeypatch.setattr(tasks, "is_monthly_recovery_window", lambda *_args: True)
    monkeypatch.setattr(
        tasks,
        "_dispatch_automatic_monthly_report_recovery",
        lambda *args: dispatched.append(args),
    )

    assert tasks._complete_monthly_measurement_and_dispatch_report(
        db, task, hospital, manifest, 2026, 8
    )
    assert manifest.closed_at is not None
    assert commits == [True]
    assert dispatched == [(db, hospital, 2026, 8)]


def test_excluded_monthly_remeasurement_stays_open_and_does_not_dispatch(
    monkeypatch,
):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="미완료의원")
    manifest = SimpleNamespace(
        cells=[], configured_platforms=["chatgpt"], closed_at=None
    )
    incidents = []
    finished = []
    monkeypatch.setattr(
        tasks,
        "summarize_manifest",
        lambda *_args, **_kwargs: SimpleNamespace(
            quality="COMPLETE",
            planned_count=1,
            success_count=1,
            failed_count=0,
            excluded_count=1,
        ),
    )
    monkeypatch.setattr(
        tasks,
        "_record_weekly_sov_failure",
        lambda *args, **kwargs: incidents.append((args, kwargs)),
    )
    monkeypatch.setattr(
        tasks,
        "_finish_sov_operation_run",
        lambda *args, **kwargs: finished.append((args, kwargs)),
    )
    monkeypatch.setattr(
        tasks,
        "_dispatch_automatic_monthly_report_recovery",
        lambda *_args: pytest.fail("incomplete measurement dispatched a report"),
    )

    assert not tasks._complete_monthly_measurement_and_dispatch_report(
        SimpleNamespace(),
        SimpleNamespace(request=SimpleNamespace(headers={})),
        hospital,
        manifest,
        2026,
        8,
    )
    assert manifest.closed_at is None
    assert incidents[0][0][2] == "MONTHLY_SOV_MEASUREMENT_INCOMPLETE"
    assert finished[0][0][2] == OperationRunState.PARTIAL


def test_monthly_report_batch_run_attaches_failure_correlation_header():
    added = []

    class _DB:
        def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

        def add(self, value):
            added.append(value)

        def get(self, _model, item_id):
            return next((row for row in added if row.id == item_id), None)

        def commit(self):
            return None

    task = SimpleNamespace(
        request=SimpleNamespace(id="monthly-batch-task", headers={})
    )
    period = SimpleNamespace(year=2026, month=8)
    db = _DB()

    run_id = tasks._start_monthly_report_batch_run(db, task, period)

    assert run_id == added[0].id
    assert added[0].task_id == "monthly-batch-task"
    assert task.request.headers["reputation_dispatch_operation_run_id"] == str(run_id)
    assert "operation_run_id" not in task.request.headers
    tasks._finish_monthly_report_batch_run(
        db,
        run_id,
        {
            "status": "FAILED",
            "total_count": 1,
            "success_count": 0,
            "failure_count": 1,
        },
        hard_failure=True,
    )
    assert added[0].state == OperationRunState.FAILED
    assert added[0].safe_error_code == "MONTHLY_REPORT_BATCH_FAILED"


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
