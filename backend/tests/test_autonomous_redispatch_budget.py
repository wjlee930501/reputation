"""자율 복구의 재배달 유예·예산·lease 인수 판정(2026-10-11, 감사 0-8·0-9)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.operations import OperationRunState
from app.workers import autonomous_recovery

NOW = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def _queued(operation_type: str, minutes: int, *, redispatch_count: int = 0, not_before=None):
    return SimpleNamespace(
        state=OperationRunState.QUEUED,
        operation_type=operation_type,
        queued_at=NOW - timedelta(minutes=minutes),
        requested_at=NOW - timedelta(minutes=minutes),
        not_before_at=not_before,
        redispatch_count=redispatch_count,
    )


@pytest.mark.parametrize("operation_type", ["TRIGGER_V0_REPORT", "GENERATE_MONTHLY_REPORT"])
def test_customer_reports_are_called_lost_after_45_minutes(operation_type) -> None:
    assert not autonomous_recovery._operation_redispatch_is_due(_queued(operation_type, 44), NOW)
    assert autonomous_recovery._operation_redispatch_is_due(_queued(operation_type, 46), NOW)


def test_other_work_keeps_the_three_hour_grace() -> None:
    assert not autonomous_recovery._operation_redispatch_is_due(_queued("RUN_SOV", 179), NOW)
    assert autonomous_recovery._operation_redispatch_is_due(_queued("RUN_SOV", 181), NOW)


@pytest.mark.parametrize(
    ("count", "not_yet", "due"),
    [(1, 89, 91), (2, 179, 181), (3, 359, 361)],
)
def test_each_redispatch_doubles_the_wait(count, not_yet, due) -> None:
    """야간 content 적체(관측 최악 ~70분) 한 번에 예산을 다 쓰지 않는다."""

    v0 = "TRIGGER_V0_REPORT"
    assert not autonomous_recovery._operation_redispatch_is_due(
        _queued(v0, not_yet, redispatch_count=count), NOW
    )
    assert autonomous_recovery._operation_redispatch_is_due(
        _queued(v0, due, redispatch_count=count), NOW
    )


def test_the_grace_stops_growing_at_the_budget() -> None:
    run = _queued("TRIGGER_V0_REPORT", 0, redispatch_count=9)
    assert autonomous_recovery._queued_redispatch_grace(run) == timedelta(minutes=45 * 8)


def test_a_deferred_run_still_follows_its_own_time() -> None:
    run = _queued("TRIGGER_V0_REPORT", 600, not_before=NOW + timedelta(minutes=1))
    assert not autonomous_recovery._operation_redispatch_is_due(run, NOW)


def _running(operation_type: str, *, redispatch_count: int = 0, lease_delta=timedelta(seconds=-1)):
    return SimpleNamespace(
        state=OperationRunState.RUNNING,
        operation_type=operation_type,
        queued_at=NOW - timedelta(hours=2),
        requested_at=NOW - timedelta(hours=2),
        lease_expires_at=NOW + lease_delta,
        not_before_at=None,
        redispatch_count=redispatch_count,
        request_payload={},
    )


def test_the_budget_counts_only_presumed_lost_copies() -> None:
    exhausted = autonomous_recovery._redispatch_budget_exhausted
    assert exhausted(_queued("RUN_SOV", 600, redispatch_count=3))
    assert exhausted(_running("RUN_SOV", redispatch_count=3))
    assert not exhausted(_queued("RUN_SOV", 600, redispatch_count=2))
    # 미룬 실행의 정시 재배달과 publish 실패(REQUESTED)는 예산을 쓰지 않는다.
    assert not exhausted(_queued("TRIGGER_V0_REPORT", 600, redispatch_count=3, not_before=NOW))
    requested = _queued("RUN_SOV", 600, redispatch_count=9)
    requested.state = OperationRunState.REQUESTED
    assert not exhausted(requested)


def _fake_redispatch_db(run):
    commits: list[bool] = []
    return SimpleNamespace(
        execute=lambda _statement: SimpleNamespace(scalar_one_or_none=lambda: run.id),
        commit=lambda: commits.append(True),
    )


def _v0_run(state, *, redispatch_count=0, not_before=None):
    hospital_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="TRIGGER_V0_REPORT",
        state=state,
        hospital_id=hospital_id,
        task_id="original-copy",
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(hospital_id),
                "queue": "reports",
                "task_args": [str(hospital_id)],
            }
        },
        requested_at=NOW - timedelta(hours=4),
        queued_at=NOW - timedelta(hours=4),
        not_before_at=not_before,
        completed_at=None,
        heartbeat_at=None,
        lease_owner=None,
        lease_expires_at=None,
        safe_error_code=None,
        safe_error_message=None,
        version=4,
        redispatch_count=redispatch_count,
    )


@pytest.mark.parametrize(
    ("state", "not_before", "expected"),
    [
        (OperationRunState.QUEUED, None, 2),
        (OperationRunState.QUEUED, NOW - timedelta(minutes=1), 1),
        (OperationRunState.REQUESTED, None, 1),
    ],
)
def test_a_redispatch_records_whether_it_spent_the_budget(
    monkeypatch, state, not_before, expected
) -> None:
    run = _v0_run(state, redispatch_count=1, not_before=not_before)
    monkeypatch.setattr(autonomous_recovery.celery_app, "send_task", lambda *_a, **_k: None)

    assert autonomous_recovery._redispatch_operation_run(_fake_redispatch_db(run), run, NOW)

    assert run.redispatch_count == expected


def test_every_idempotent_policy_type_is_taken_over_and_recertify_is_not() -> None:
    """새 재배달 정책을 더하면 인수 여부를 여기서 명시적으로 정해야 한다."""

    assert autonomous_recovery._LEASE_TAKEOVER_TYPES == (
        set(autonomous_recovery._OPERATION_REDISPATCH_POLICIES) - {"RECERTIFY_PUBLISHED_IMAGE"}
    )


@pytest.mark.parametrize(
    "operation_type",
    [
        "RUN_SOV",
        "GENERATE_MONTHLY_REPORT",
        "REGENERATE_CONTENT",
        "REGENERATE_CONTENT_IMAGE",
        "GENERATE_CONTENT_ITEM",
        "TRIGGER_V0_REPORT",
        "REBUILD_SITE",
    ],
)
def test_an_expired_lease_is_due_for_takeover(operation_type) -> None:
    assert autonomous_recovery._operation_redispatch_is_due(_running(operation_type), NOW)
    assert not autonomous_recovery._operation_redispatch_is_due(
        _running(operation_type, lease_delta=timedelta(seconds=1)), NOW
    )


def test_recertify_stays_with_its_own_sweep() -> None:
    assert not autonomous_recovery._operation_redispatch_is_due(
        _running("RECERTIFY_PUBLISHED_IMAGE"), NOW
    )


def test_a_monthly_rebuild_is_not_rerun_after_its_worker_died() -> None:
    refused = autonomous_recovery._lease_takeover_refused
    rebuild = _running("GENERATE_MONTHLY_REPORT")
    rebuild.request_payload = {"_dispatch": {"task_args": ["h", 2026, 9, True]}}
    plain = _running("GENERATE_MONTHLY_REPORT")
    plain.request_payload = {"_dispatch": {"task_args": ["h", 2026, 9]}}
    assert refused(rebuild)
    assert not refused(plain)
    assert not refused(_running("RUN_SOV"))
