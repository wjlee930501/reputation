"""A monthly chunk boundary continues the same run; it is not a measurement failure."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from app.models.hospital import HospitalStatus
from app.workers import tasks


class _Retried(Exception):
    pass


class _DB:
    def __init__(self, hospital):
        self.hospital = hospital
        self.commits = 0
        self.rollbacks = 0

    def get(self, _model, _id):
        return self.hospital

    def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _slot(*, terminal: bool):
    return SimpleNamespace(
        id=uuid.uuid4(),
        answer_status="RECEIVED" if terminal else "PENDING",
        judgment_status="CONFIRMED" if terminal else "PENDING",
        answer_attempt_count=1 if terminal else 0,
        judgment_attempt_count=1 if terminal else 0,
    )


def _spec(cell):
    return {
        "query_id": uuid.uuid4(),
        "query_text": "강남 정형외과 추천",
        "platform": "chatgpt",
        "target_id": uuid.uuid4(),
        "variant_id": uuid.uuid4(),
        "manifest_cell": cell,
    }


def _shell(monkeypatch, *, measurement_mode="monthly", retries=0, slots_per_cell=None):
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="이어가기의원",
        status=HospitalStatus.ACTIVE,
        competitors=[],
        region=["서울"],
    )
    cells = [SimpleNamespace(id=uuid.uuid4(), state="PENDING") for _ in range(2)]
    specs = [_spec(cell) for cell in cells]
    slots_per_cell = slots_per_cell or {
        cell.id: [_slot(terminal=False), _slot(terminal=False)] for cell in cells
    }
    calls = SimpleNamespace(
        retries=[], incidents=[], finished=[], executed=[], recovered=[], db=None
    )

    def fake_retry(**kwargs):
        calls.retries.append(kwargs)
        raise _Retried()

    task = SimpleNamespace(
        retry=fake_retry,
        max_retries=1,
        request=SimpleNamespace(
            headers={},
            id="worker-task",
            kwargs={},
            retries=retries,
            operation_run_claim_version=1,
        ),
    )

    def session():
        calls.db = _DB(hospital)
        return calls.db

    monkeypatch.setattr(tasks, "SyncSessionLocal", session)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "_operation_run_claimed_or_legacy", lambda *_a: True)
    monkeypatch.setattr(
        tasks, "_sov_measurement_mode_from_operation_run", lambda *_a: measurement_mode
    )
    monkeypatch.setattr(tasks, "hospital_in_monthly_cohort", lambda *_a, **_k: True)
    monkeypatch.setattr(tasks, "tracking_set_members", lambda targets: targets)
    monkeypatch.setattr(tasks, "_build_measurement_specs", lambda **_k: (specs, 0))
    manifest = SimpleNamespace(cells=cells)
    monkeypatch.setattr(tasks, "freeze_dispatch_manifest", lambda *_a, **_k: manifest)
    monkeypatch.setattr(
        tasks, "reopen_incomplete_manifest_for_recovery", lambda *_a, **_k: False
    )
    monkeypatch.setattr(tasks, "_pending_weekly_manifest_specs", lambda _m, _s: specs)
    monkeypatch.setattr(tasks, "_manifest_execution_policy_matches", lambda *_a: True)
    monkeypatch.setattr(tasks, "_manifest_slot_repeat_count", lambda *_a: 2)
    monkeypatch.setattr(
        tasks,
        "_start_measurement_run",
        lambda *_a, **_k: SimpleNamespace(
            id=uuid.uuid4(), config={"measurement_protocol": {"version": "t"}}
        ),
    )
    monkeypatch.setattr(
        tasks,
        "ensure_monthly_slots",
        lambda _db, *, cell, **_k: slots_per_cell[cell.id],
    )
    monkeypatch.setattr(tasks, "_finish_measurement_run", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "_refresh_exposure_actions_sync", lambda *_a: None)
    monkeypatch.setattr(
        tasks,
        "_record_weekly_sov_failure",
        lambda *args, **kwargs: calls.incidents.append((args, kwargs)),
    )
    monkeypatch.setattr(
        tasks,
        "_finish_sov_operation_run",
        lambda *args, **kwargs: calls.finished.append((args, kwargs)),
    )
    monkeypatch.setattr(
        tasks,
        "open_monthly_sov_failure",
        lambda **_k: pytest.fail("chunk continuation opened a monthly incident"),
    )

    async def recover(**kwargs):
        calls.recovered.append(kwargs)

    monkeypatch.setattr(tasks, "_recover_sov_failure", recover)
    monkeypatch.setattr(
        tasks,
        "_complete_monthly_measurement_and_dispatch_report",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_a, **_k: tasks.arrow.get(2026, 8, 31, tzinfo="Asia/Seoul"),
    )

    def execute(_db, *, slot, **_kwargs):
        calls.executed.append(slot.id)
        slot.answer_status = "RECEIVED"
        slot.judgment_status = "CONFIRMED"
        return {"measurement_status": "SUCCESS"}

    monkeypatch.setattr(tasks, "_execute_paid_observation_slot", execute)
    return task, hospital, cells, slots_per_cell, calls


def _run(task, hospital, **kwargs):
    body = getattr(tasks.run_sov_for_hospital.run, "__func__", tasks.run_sov_for_hospital.run)
    return body(task, str(hospital.id), **kwargs)


def _deadline_after(monkeypatch, checks_before_stop: int):
    seen = {"count": 0}

    def reached(_started_at):
        seen["count"] += 1
        return seen["count"] > checks_before_stop

    monkeypatch.setattr(tasks, "_sov_chunk_deadline_reached", reached)


def _assert_continuation(calls, *, failure_retry_count=0):
    assert calls.incidents == []
    assert calls.finished == []
    assert len(calls.retries) == 1
    retry = calls.retries[0]
    assert retry["max_retries"] == tasks.SOV_CONTINUATION_MAX_RETRIES
    assert retry["countdown"] == tasks.SOV_CONTINUATION_COUNTDOWN_SECONDS
    assert retry["kwargs"]["failure_retry_count"] == failure_retry_count


def test_chunk_stop_between_cells_continues_without_incident(monkeypatch):
    task, hospital, cells, slots, calls = _shell(monkeypatch)
    # spec 0 start, two slot checks, then the post-spec check stops the chunk.
    _deadline_after(monkeypatch, 3)

    with pytest.raises(_Retried):
        _run(task, hospital)

    _assert_continuation(calls)
    assert calls.executed == [slot.id for slot in slots[cells[0].id]]
    assert cells[0].state == "SUCCESS"
    assert cells[1].state == "PENDING"


def test_chunk_stop_between_slots_does_not_start_the_next_paid_slot(monkeypatch):
    task, hospital, cells, slots, calls = _shell(monkeypatch)
    # spec 0 start and first slot pass; the second slot check stops the chunk.
    _deadline_after(monkeypatch, 2)

    with pytest.raises(_Retried):
        _run(task, hospital)

    _assert_continuation(calls)
    assert calls.executed == [slots[cells[0].id][0].id]
    assert cells[0].state == "PENDING"
    assert calls.db.rollbacks == 1


def test_chunk_stop_before_first_cell_continues_without_incident(monkeypatch):
    task, hospital, _cells, _slots, calls = _shell(monkeypatch)
    _deadline_after(monkeypatch, 0)

    with pytest.raises(_Retried):
        _run(task, hospital)

    _assert_continuation(calls)
    assert calls.executed == []


def test_soft_time_limit_inside_slot_continues_without_incident(monkeypatch):
    task, hospital, cells, slots, calls = _shell(monkeypatch)
    monkeypatch.setattr(tasks, "_sov_chunk_deadline_reached", lambda *_a: False)

    def interrupted(_db, *, slot, **_kwargs):
        calls.executed.append(slot.id)
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(tasks, "_execute_paid_observation_slot", interrupted)

    with pytest.raises(_Retried):
        _run(task, hospital)

    _assert_continuation(calls)
    assert isinstance(calls.retries[0]["exc"].__cause__, SoftTimeLimitExceeded)
    assert calls.executed == [slots[cells[0].id][0].id]


def test_soft_time_limit_outside_the_cell_loop_continues(monkeypatch):
    task, hospital, _cells, _slots, calls = _shell(monkeypatch)

    def interrupted(*_args, **_kwargs):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(tasks, "ensure_monthly_slots", interrupted)

    with pytest.raises(_Retried):
        _run(task, hospital)

    _assert_continuation(calls)
    assert isinstance(calls.retries[0]["exc"], SoftTimeLimitExceeded)


def test_continuation_keeps_the_failure_retry_budget(monkeypatch):
    task, hospital, _cells, _slots, calls = _shell(monkeypatch, retries=7)
    _deadline_after(monkeypatch, 0)

    with pytest.raises(_Retried):
        _run(task, hospital, failure_retry_count=1)

    _assert_continuation(calls, failure_retry_count=1)


def test_reentry_after_continuation_measures_only_remaining_slots(monkeypatch):
    task, hospital, cells, slots, calls = _shell(monkeypatch)
    _deadline_after(monkeypatch, 2)
    with pytest.raises(_Retried):
        _run(task, hospital)
    first_chunk = list(calls.executed)
    assert first_chunk == [slots[cells[0].id][0].id]

    retried = SimpleNamespace(**vars(task))
    retried.request = SimpleNamespace(**{**vars(task.request), "retries": 1})
    calls.retries.clear()
    calls.executed.clear()
    monkeypatch.setattr(tasks, "_sov_chunk_deadline_reached", lambda *_a: False)
    real_is_terminal = tasks.slot_is_terminal

    def execute_resuming(_db, *, slot, **_kwargs):
        if real_is_terminal(slot):
            return {"measurement_status": "SUCCESS"}
        calls.executed.append(slot.id)
        slot.answer_status = "RECEIVED"
        slot.judgment_status = "CONFIRMED"
        return {"measurement_status": "SUCCESS"}

    monkeypatch.setattr(tasks, "_execute_paid_observation_slot", execute_resuming)

    _run(retried, hospital)

    remaining = [slots[cells[0].id][1].id, *[slot.id for slot in slots[cells[1].id]]]
    assert calls.executed == remaining
    assert set(first_chunk).isdisjoint(calls.executed)
    assert calls.retries == []
    assert calls.incidents == []
    assert calls.finished == []
    assert [cell.state for cell in cells] == ["SUCCESS", "SUCCESS"]
    assert len(calls.recovered) == 1


def test_exhausted_continuations_fall_back_to_partial_incident(monkeypatch):
    task, hospital, _cells, _slots, calls = _shell(
        monkeypatch, retries=tasks.SOV_CONTINUATION_MAX_RETRIES
    )
    _deadline_after(monkeypatch, 0)

    _run(task, hospital)

    assert calls.retries == []
    assert calls.incidents[0][0][2] == "MONTHLY_SOV_MEASUREMENT_PARTIAL"
    assert calls.finished[0][0][2] == tasks.OperationRunState.PARTIAL


def test_real_slot_failures_still_open_partial_incident(monkeypatch):
    task, hospital, _cells, _slots, calls = _shell(monkeypatch)
    monkeypatch.setattr(tasks, "_sov_chunk_deadline_reached", lambda *_a: False)
    monkeypatch.setattr(tasks, "_weekly_manifest_is_resolved", lambda *_a: False)

    def failed(_db, *, slot, **_kwargs):
        slot.answer_attempt_count += 1
        return {"measurement_status": "FAILED", "failure_reason": "provider_error"}

    monkeypatch.setattr(tasks, "_execute_paid_observation_slot", failed)

    _run(task, hospital)

    assert calls.retries == []
    assert calls.incidents[0][0][2] == "MONTHLY_SOV_MEASUREMENT_PARTIAL"
    assert calls.finished[0][0][2] == tasks.OperationRunState.PARTIAL


def test_weekly_chunk_stop_keeps_partial_incident(monkeypatch):
    task, hospital, _cells, _slots, calls = _shell(monkeypatch, measurement_mode="weekly")
    _deadline_after(monkeypatch, 0)

    _run(task, hospital)

    assert calls.retries == []
    assert calls.incidents[0][0][2] == "WEEKLY_SOV_MEASUREMENT_PARTIAL"
    assert calls.finished[0][0][2] == tasks.OperationRunState.PARTIAL


def test_monthly_unexpected_error_uses_its_own_failure_budget(monkeypatch):
    task, hospital, _cells, _slots, calls = _shell(monkeypatch, retries=12)

    def boom(*_args, **_kwargs):
        raise RuntimeError("transient")

    monkeypatch.setattr(tasks, "ensure_monthly_slots", boom)

    with pytest.raises(_Retried):
        _run(task, hospital)
    assert calls.retries[0]["countdown"] == 300
    assert calls.retries[0]["max_retries"] == tasks.SOV_CONTINUATION_MAX_RETRIES
    assert calls.retries[0]["kwargs"]["failure_retry_count"] == 1

    calls.retries.clear()
    with pytest.raises(RuntimeError, match="transient"):
        _run(task, hospital, failure_retry_count=1)
    assert calls.retries == []
