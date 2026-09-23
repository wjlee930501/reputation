"""Paid SoV slot settlement uses actual provider attempts; cost denial is not PARTIAL."""

import uuid
from types import SimpleNamespace

import pytest

from app.api.admin import operations_center_serializers as serializers
from app.models.hospital import HospitalStatus
from app.models.operations import OperationRunState
from app.workers import tasks
from app.workers import weekly_sov_incident_control as incident_control


def _answer_slot():
    return SimpleNamespace(
        id=uuid.uuid4(),
        answer_status="PENDING",
        answer_attempt_count=1,
        answer_failure_reason=None,
        platform="chatgpt",
        scope="MONTHLY",
        measurement_run_id=uuid.uuid4(),
    )


def _patch_answer_stage(monkeypatch, slot, provider_calls):
    settled = []

    async def reserve(category, *, count, reservation_id):
        assert (category, count) == ("sov", 1)
        return SimpleNamespace(allowed=True, receipt=reservation_id)

    async def settle(receipt, *, consumed_units):
        settled.append(consumed_units)

    async def fetch(*_args, **_kwargs):
        return {
            "measurement_status": "FAILED",
            "failure_reason": "provider_query_failed:TimeoutError",
            "provider_calls": provider_calls,
        }

    def checkpoint(_db, _slot_id, _answer, *, lease_token):
        slot.answer_status = "FAILED"
        return slot

    monkeypatch.setattr(tasks, "slot_needs_answer", lambda _slot: True)
    monkeypatch.setattr(
        tasks, "claim_slot_stage", lambda _db, _slot_id, *, stage: (slot, uuid.uuid4())
    )
    monkeypatch.setattr(tasks.cost_guard, "reserve", reserve)
    monkeypatch.setattr(tasks.cost_guard, "settle_reservation", settle)
    monkeypatch.setattr(tasks, "fetch_answer", fetch)
    monkeypatch.setattr(tasks, "checkpoint_answer", checkpoint)
    return settled


class _NoopDB:
    def commit(self):
        return None

    def rollback(self):
        return None


@pytest.mark.parametrize("provider_calls, consumed", [(0, 0), (1, 1), (3, 1)])
def test_answer_settlement_uses_actual_provider_attempts(monkeypatch, provider_calls, consumed):
    slot = _answer_slot()
    settled = _patch_answer_stage(monkeypatch, slot, provider_calls)

    tasks._execute_paid_observation_slot(
        _NoopDB(),
        slot=slot,
        hospital=SimpleNamespace(id=uuid.uuid4(), name="가나의원", region=["강남"]),
        query_text="강남 내과 추천",
        competitors=[],
        protocol={"openai_model_query": "openai/gpt-5.6-luna"},
    )

    # Zero attempts releases the reservation; retries beyond it stay in the actual-call meter.
    assert settled == [consumed]


def _patch_slotted_run(monkeypatch, slot_results):
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="가나의원",
        status=HospitalStatus.ACTIVE,
        competitors=[],
        region=["강남"],
    )
    cell = SimpleNamespace(id=uuid.uuid4(), state="FAILED")
    spec = {
        "query_text": "강남 내과 추천",
        "platform": "chatgpt",
        "query_id": uuid.uuid4(),
        "target_id": None,
        "variant_id": None,
        "manifest_cell": cell,
    }
    slots = [SimpleNamespace(id=uuid.uuid4(), judgment_status="PENDING") for _ in slot_results]
    executed = []
    incidents = []
    finished = []

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _DB:
        def get(self, _model, _id):
            return hospital

        def execute(self, _stmt):
            return _Result()

        def commit(self):
            return None

        def rollback(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def execute_slot(_db, *, slot, **_kwargs):
        executed.append(slot.id)
        return slot_results[len(executed) - 1]

    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "_operation_run_claimed_or_legacy", lambda _task: True)
    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "_build_measurement_specs", lambda **_kwargs: ([spec], 0))
    monkeypatch.setattr(
        tasks, "freeze_dispatch_manifest", lambda *_args, **_kwargs: SimpleNamespace()
    )
    monkeypatch.setattr(tasks, "_pending_weekly_manifest_specs", lambda *_args: [spec])
    monkeypatch.setattr(tasks, "_manifest_execution_policy_matches", lambda _manifest: True)
    monkeypatch.setattr(
        tasks,
        "_start_measurement_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            id=uuid.uuid4(), config={"measurement_protocol": {"version": "test"}}
        ),
    )
    monkeypatch.setattr(tasks, "_manifest_slot_repeat_count", lambda _manifest: len(slots))
    monkeypatch.setattr(tasks, "ensure_monthly_slots", lambda *_args, **_kwargs: slots)
    monkeypatch.setattr(tasks, "_sov_chunk_deadline_reached", lambda _started: False)
    monkeypatch.setattr(tasks, "_execute_paid_observation_slot", execute_slot)
    monkeypatch.setattr(tasks, "_refresh_exposure_actions_sync", lambda _hospital_id: None)
    monkeypatch.setattr(
        tasks, "_record_weekly_sov_failure", lambda *args, **kwargs: incidents.append(args[2])
    )
    monkeypatch.setattr(
        tasks,
        "_finish_sov_operation_run",
        lambda _db, _task, state, code, message: finished.append((state, code, message)),
    )
    return hospital, executed, incidents, finished


def test_slot_cost_denial_stops_as_failed_cost_guard_blocked_not_partial(monkeypatch):
    blocked = {"measurement_status": "FAILED", "failure_reason": "cost_guard_blocked"}
    unreachable = {"measurement_status": "SUCCESS"}
    hospital, executed, incidents, finished = _patch_slotted_run(
        monkeypatch, [blocked, unreachable]
    )

    tasks.run_sov_for_hospital.run(str(hospital.id), "weekly")

    assert len(executed) == 1, "cost denial must stop further paid slots"
    assert incidents == ["WEEKLY_SOV_COST_GUARD_BLOCKED"]
    assert [(state, code) for state, code, _ in finished] == [
        (OperationRunState.FAILED, "WEEKLY_SOV_COST_GUARD_BLOCKED")
    ]
    assert "부분" not in finished[0][2]


def test_ordinary_slot_failure_is_still_partial(monkeypatch):
    failed = {"measurement_status": "FAILED", "failure_reason": "provider_query_failed:Timeout"}
    hospital, executed, incidents, finished = _patch_slotted_run(monkeypatch, [failed, failed])

    tasks.run_sov_for_hospital.run(str(hospital.id), "weekly")

    assert len(executed) == 2
    assert incidents == ["WEEKLY_SOV_MEASUREMENT_PARTIAL"]
    assert [(state, code) for state, code, _ in finished] == [
        (OperationRunState.PARTIAL, "WEEKLY_SOV_MEASUREMENT_PARTIAL")
    ]


@pytest.mark.parametrize("prefix", ["WEEKLY_SOV", "MONTHLY_SOV"])
def test_cost_block_messages_are_distinct_from_partial(prefix):
    cost = f"{prefix}_COST_GUARD_BLOCKED"
    partial = f"{prefix}_MEASUREMENT_PARTIAL"
    period_label = "이번 달" if prefix == "MONTHLY_SOV" else "이번 주"

    assert incident_control._safe_message(cost) != incident_control._safe_message(partial)
    assert "비용" in incident_control._safe_message(cost)
    assert "부분" not in incident_control._safe_message(cost)
    assert "비용" not in incident_control._safe_message(partial)
    assert "비용" in incident_control._next_action(cost, period_label)
    assert "비용" not in incident_control._next_action(partial, period_label)
    assert tasks._sov_operation_error_message(cost) != tasks._sov_operation_error_message(partial)
    assert "비용" not in tasks._sov_operation_error_message(partial)


@pytest.mark.parametrize("prefix", ["WEEKLY_SOV", "MONTHLY_SOV"])
def test_operations_center_groups_cost_block_as_cost_not_partial(prefix):
    cost = serializers.canonical_cause_code(f"{prefix}_COST_GUARD_BLOCKED", "")
    partial = serializers.canonical_cause_code(f"{prefix}_MEASUREMENT_PARTIAL", "")

    assert cost == "COST_LIMIT_EXHAUSTED"
    assert partial == f"{prefix}_MEASUREMENT_PARTIAL"
