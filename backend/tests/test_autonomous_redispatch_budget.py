"""자율 복구의 재배달 유예·예산·lease 인수 판정(2026-10-11, 감사 0-8·0-9)."""

from __future__ import annotations

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
