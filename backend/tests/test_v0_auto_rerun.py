"""V0 최종 실패는 같은 실행으로 6시간 뒤 다시 돈다 — 3회 소진 뒤에만 사람에게 알린다(Phase 1-9a)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.incident_types import SWEEP_OWNED_INCIDENT_TYPES, incident_policy
from app.workers import tasks
from app.workers.incident_backlog import _RETRYING_RESOLVABLE

NOW = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def _patch(monkeypatch, *, rerun_at):
    opened, finished = [], []

    async def fake_open(**kwargs):
        opened.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(tasks, "open_ops_incident", fake_open)
    monkeypatch.setattr(tasks, "_schedule_v0_auto_rerun", lambda _task, now=None: rerun_at)
    monkeypatch.setattr(tasks, "_finish_v0_run_failed", lambda _task, _hid: finished.append(True))
    return opened, finished


def test_a_retryable_final_failure_schedules_a_rerun_and_opens_retrying(monkeypatch):
    rerun_at = NOW + tasks.V0_AUTO_RERUN_INTERVAL
    opened, finished = _patch(monkeypatch, rerun_at=rerun_at)

    outcome = tasks._handle_v0_final_failure(
        SimpleNamespace(), str(uuid.uuid4()), RuntimeError("pdf render failed")
    )

    assert outcome == {"status": "auto_rerun_scheduled", "not_before": rerun_at.isoformat()}
    assert opened[0]["incident_type"] == "V0_REPORT_FAILED"
    assert opened[0]["retry_until"] == rerun_at + tasks.V0_AUTO_RERUN_GRACE
    assert opened[0]["next_action"] == tasks.V0_AUTO_RERUN_NEXT_ACTION
    assert finished == []


def test_an_exhausted_budget_opens_for_people_and_terminalizes_once(monkeypatch):
    opened, finished = _patch(monkeypatch, rerun_at=None)

    outcome = tasks._handle_v0_final_failure(
        SimpleNamespace(), str(uuid.uuid4()), RuntimeError("pdf render failed")
    )

    assert outcome is None
    assert opened[0]["retry_until"] is None
    assert opened[0]["safe_error_code"] == "V0_REPORT_RETRIES_EXHAUSTED"
    assert finished == [True]


def test_policy_drift_is_never_rerun(monkeypatch):
    opened, finished = _patch(monkeypatch, rerun_at=NOW + timedelta(hours=6))
    monkeypatch.setattr(
        tasks, "_schedule_v0_auto_rerun", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError)
    )

    outcome = tasks._handle_v0_final_failure(
        SimpleNamespace(), str(uuid.uuid4()), tasks.V0MeasurementPolicyDrift("drift")
    )

    assert outcome is None
    assert opened[0]["safe_error_code"] == "V0_MEASUREMENT_POLICY_DRIFT"
    assert opened[0]["retry_until"] is None
    assert finished == [True]


def test_an_incident_failure_does_not_lose_the_rerun(monkeypatch):
    rerun_at = NOW + tasks.V0_AUTO_RERUN_INTERVAL
    _opened, finished = _patch(monkeypatch, rerun_at=rerun_at)

    async def broken_open(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(tasks, "open_ops_incident", broken_open)

    outcome = tasks._handle_v0_final_failure(SimpleNamespace(), str(uuid.uuid4()), RuntimeError("x"))

    assert outcome["status"] == "auto_rerun_scheduled"
    assert finished == []


def test_rerun_budget_reads_fail_closed():
    assert tasks._v0_auto_reruns_used({}) == 0
    assert tasks._v0_auto_reruns_used({"v0_auto_reruns": 2}) == 2
    assert tasks._v0_auto_reruns_used({"v0_auto_reruns": "2"}) == tasks.V0_AUTO_RERUN_BUDGET
    assert tasks._v0_auto_reruns_used({"v0_auto_reruns": -1}) == tasks.V0_AUTO_RERUN_BUDGET
    assert tasks._v0_auto_reruns_used(None) == 0


def test_v0_failure_is_owned_by_the_rerun_until_the_caller_deadline():
    """기한은 재실행 시각 + 유예(호출자). 없으면(소진·측정 기준 변경) 사람의 일이다."""
    assert "V0_REPORT_FAILED" in SWEEP_OWNED_INCIDENT_TYPES
    assert incident_policy("V0_REPORT_FAILED").retry_window is None
    # 재실행 중(RETRYING)인 사고도 V0 리포트가 생기면 근거 정리가 닫는다.
    assert "V0_REPORT_FAILED" in _RETRYING_RESOLVABLE


class _NoSession:
    def __enter__(self):
        return object()

    def __exit__(self, *_args):
        return False


def _claimed_task():
    return SimpleNamespace(
        request=SimpleNamespace(
            id="v0-copy",
            headers={"operation_run_id": str(uuid.uuid4())},
            operation_run_claim_version=3,
            kwargs={},
        ),
        retry=lambda **kwargs: RuntimeError("retry"),
    )


def test_rerun_grace_covers_a_six_hour_wait():
    assert tasks.V0_AUTO_RERUN_GRACE == timedelta(hours=6)


def test_a_cost_deferral_extends_the_retrying_deadline(monkeypatch):
    """재실행이 비용 창 때문에 다시 미뤄지면 사고 기한도 그 미룸 시각 + 유예로 민다."""
    hospital_id = str(uuid.uuid4())
    deferred, extended = [], []
    monkeypatch.setattr(tasks, "SyncSessionLocal", _NoSession)
    monkeypatch.setattr(
        tasks,
        "defer_operation_run",
        lambda _db, _task, not_before: deferred.append(not_before) or True,
    )

    async def fake_extend(**kwargs):
        extended.append(kwargs)
        return True

    monkeypatch.setattr(tasks, "extend_retrying_deadline", fake_extend)

    result = tasks._defer_v0_until_cost_window(
        _claimed_task(), RuntimeError("cost"), 0, hospital_id=hospital_id
    )

    assert result["status"] == "cost_deferred"
    assert extended == [
        {
            "pipeline": "v0_report",
            "object_type": "hospital",
            "object_id": hospital_id,
            "fingerprint": tasks.IncidentFingerprint.UNKNOWN,
            "retry_until": deferred[0] + tasks.V0_AUTO_RERUN_GRACE,
        }
    ]


def test_a_stale_deferral_does_not_touch_the_incident(monkeypatch):
    monkeypatch.setattr(tasks, "SyncSessionLocal", _NoSession)
    monkeypatch.setattr(tasks, "defer_operation_run", lambda *_a: False)

    async def fake_extend(**_kwargs):
        raise AssertionError("must not extend")

    monkeypatch.setattr(tasks, "extend_retrying_deadline", fake_extend)

    result = tasks._defer_v0_until_cost_window(
        _claimed_task(), RuntimeError("cost"), 0, hospital_id=str(uuid.uuid4())
    )

    assert result["status"] == "stale_run"
