import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.operations import OperationRunState
from app.services import monthly_remasure_gate as gate
from app.workers import tasks

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _slot(repeat_no, *, answer="PENDING", judgment="PENDING", answered_at=None,
          completed_at=None, updated_at=None, answer_attempts=0, judgment_attempts=0):
    return SimpleNamespace(
        repeat_no=repeat_no,
        answer_status=answer,
        judgment_status=judgment,
        answer_attempt_count=answer_attempts,
        judgment_attempt_count=judgment_attempts,
        answered_at=answered_at,
        completed_at=completed_at,
        updated_at=updated_at or NOW - timedelta(days=5),
    )


def _manifest(cells, *, repeat_count=5, frozen_hours_ago=48):
    return SimpleNamespace(
        frozen_at=NOW - timedelta(hours=frozen_hours_ago),
        platform_provenance={"observation_slots": {"repeat_count": repeat_count}},
        cells=cells,
    )


def _cell(slots, *, state="FAILED", attempts=()):
    return SimpleNamespace(state=state, observation_slots=slots, attempts=list(attempts))


def _auto_run(state=OperationRunState.QUEUED, *, error_code=None, lease_expires_at=None):
    return SimpleNamespace(
        state=state, safe_error_code=error_code, lease_expires_at=lease_expires_at
    )


def _progress(run, *, remaining=4, hours_ago=13):
    return gate.MonthlyRecoveryProgress(
        automatic_run=run,
        remaining_work=remaining,
        last_progress_at=NOW - timedelta(hours=hours_ago),
    )


def test_remaining_work_counts_missing_and_pending_slots_but_not_exhausted_ones():
    confirmed = _slot(1, answer="RECEIVED", judgment="CONFIRMED", completed_at=NOW)
    exhausted = _slot(2, answer="FAILED", answer_attempts=3)
    pending = _slot(3)
    manifest = _manifest(
        [
            _cell([confirmed, exhausted, pending]),
            _cell([], state="EXCLUDED"),
        ],
        repeat_count=5,
    )

    # repeats 4 and 5 were never materialized; repeat 3 is still pending.
    assert gate.manifest_remaining_work(manifest) == 3


def test_lease_claim_on_pending_slot_is_not_progress():
    claimed_recently = _slot(1, answer_attempts=1, updated_at=NOW - timedelta(minutes=5))
    manifest = _manifest([_cell([claimed_recently])], frozen_hours_ago=30)

    assert gate.manifest_last_progress_at(manifest) == NOW - timedelta(hours=30)


def test_received_answer_and_legacy_attempt_links_are_progress():
    answered = _slot(1, answer="RECEIVED", answered_at=NOW - timedelta(hours=3))
    legacy_link = SimpleNamespace(linked_at=NOW - timedelta(hours=1))
    manifest = _manifest([_cell([answered], attempts=[legacy_link])])

    assert gate.manifest_last_progress_at(manifest) == NOW - timedelta(hours=1)


def test_default_is_locked_without_an_automatic_run():
    decision = gate.decide_manual_remasure(
        gate.MonthlyRecoveryProgress(None, 10, NOW - timedelta(days=2)), now=NOW
    )

    assert decision.allowed is False
    assert decision.code == gate.LOCKED


@pytest.mark.parametrize(
    "run, remaining",
    [
        (_auto_run(OperationRunState.SUCCEEDED), 4),
        (_auto_run(OperationRunState.FAILED, error_code="MONTHLY_SOV_COST_GUARD_BLOCKED"), 4),
        (_auto_run(OperationRunState.RUNNING, lease_expires_at=NOW + timedelta(minutes=5)), 4),
        (_auto_run(OperationRunState.QUEUED), 0),
        (_auto_run(OperationRunState.QUEUED), None),
    ],
)
def test_stalled_clock_alone_does_not_unlock(run, remaining):
    decision = gate.decide_manual_remasure(_progress(run, remaining=remaining), now=NOW)

    assert decision.allowed is False
    assert decision.code == gate.LOCKED


def test_twelve_hours_without_manifest_progress_unlocks():
    decision = gate.decide_manual_remasure(_progress(_auto_run(), hours_ago=12), now=NOW)

    assert decision.allowed is True
    assert decision.code == gate.STALL_UNLOCKED
    assert decision.remaining_work == 4


def test_gate_staleness_is_independent_of_coverage_recovery_staleness():
    run = _auto_run(OperationRunState.QUEUED)
    run.queued_at = NOW - timedelta(hours=2)
    run.requested_at = run.queued_at
    assert tasks._coverage_recovery_run_is_stale(run, NOW) is True

    decision = gate.decide_manual_remasure(_progress(run, hours_ago=2), now=NOW)

    assert gate.MANUAL_REMASURE_STALL_THRESHOLD == timedelta(hours=12)
    assert decision.allowed is False


async def test_authorization_allows_once_and_replays_the_same_request(monkeypatch):
    hospital_id = uuid.uuid4()
    runs = []

    async def prior_runs(_db, *_args):
        return list(runs)

    async def progress(_db, *_args):
        return _progress(_auto_run())

    monkeypatch.setattr(gate, "_prior_manual_remasure_runs", prior_runs)
    monkeypatch.setattr(gate, "load_monthly_recovery_progress", progress)

    allowed = await gate.authorize_manual_remasure(
        None, hospital_id=hospital_id, year=2026, month=8,
        request_fingerprint="first", now=NOW,
    )
    assert allowed.operation_key == f"monthly-sov-remasure:{hospital_id}:2026-08:stall-unlock"
    assert allowed.request_payload_extra["manual_remasure"]["request_fingerprint"] == "first"

    runs.append(
        SimpleNamespace(
            idempotency_key=allowed.operation_key,
            request_payload=allowed.request_payload_extra,
            state=OperationRunState.QUEUED,
            safe_error_code=None,
        )
    )
    replay = await gate.authorize_manual_remasure(
        None, hospital_id=hospital_id, year=2026, month=8,
        request_fingerprint="first", now=NOW,
    )
    assert replay.operation_key == allowed.operation_key
    assert replay.request_payload_extra is None

    with pytest.raises(gate.ManualRemasureLocked) as exc:
        await gate.authorize_manual_remasure(
            None, hospital_id=hospital_id, year=2026, month=8,
            request_fingerprint="second", now=NOW,
        )
    assert exc.value.decision.code == gate.ALREADY_USED


async def test_earlier_manual_remasure_spends_the_single_allowance(monkeypatch):
    hospital_id = uuid.uuid4()
    legacy = SimpleNamespace(
        idempotency_key=f"monthly-sov-remasure:{hospital_id}:2026-08:abc123",
        request_payload={},
        state=OperationRunState.SUCCEEDED,
        safe_error_code=None,
    )

    async def prior_runs(_db, *_args):
        return [legacy]

    async def progress(_db, *_args):
        pytest.fail("a spent allowance must not re-evaluate the stall")

    monkeypatch.setattr(gate, "_prior_manual_remasure_runs", prior_runs)
    monkeypatch.setattr(gate, "load_monthly_recovery_progress", progress)

    with pytest.raises(gate.ManualRemasureLocked) as exc:
        await gate.authorize_manual_remasure(
            None, hospital_id=hospital_id, year=2026, month=8,
            request_fingerprint="new-click", now=NOW,
        )
    assert exc.value.decision.code == gate.ALREADY_USED


async def test_broker_rejected_remasure_does_not_spend_the_allowance(monkeypatch):
    hospital_id = uuid.uuid4()
    prefix = f"monthly-sov-remasure:{hospital_id}:2026-08:"
    broker_rejected = SimpleNamespace(
        idempotency_key=f"{prefix}stall-unlock",
        request_payload={"manual_remasure": {"request_fingerprint": "first"}},
        state=OperationRunState.FAILED,
        safe_error_code="BROKER_UNAVAILABLE",
    )
    runs = [broker_rejected]

    async def prior_runs(_db, *_args):
        return list(runs)

    async def progress(_db, *_args):
        return _progress(_auto_run())

    monkeypatch.setattr(gate, "_prior_manual_remasure_runs", prior_runs)
    monkeypatch.setattr(gate, "load_monthly_recovery_progress", progress)

    # The same click is not replayed onto the dead run; it gets a fresh, dispatchable key.
    retried = await gate.authorize_manual_remasure(
        None, hospital_id=hospital_id, year=2026, month=8,
        request_fingerprint="first", now=NOW,
    )
    assert retried.operation_key == f"{prefix}stall-unlock:2"
    assert retried.request_payload_extra["manual_remasure"]["request_fingerprint"] == "first"

    runs.append(
        SimpleNamespace(
            idempotency_key=retried.operation_key,
            request_payload=retried.request_payload_extra,
            state=OperationRunState.QUEUED,
            safe_error_code=None,
        )
    )
    with pytest.raises(gate.ManualRemasureLocked) as exc:
        await gate.authorize_manual_remasure(
            None, hospital_id=hospital_id, year=2026, month=8,
            request_fingerprint="second", now=NOW,
        )
    assert exc.value.decision.code == gate.ALREADY_USED


async def test_broker_rejected_remasure_still_requires_a_stall(monkeypatch):
    hospital_id = uuid.uuid4()
    broker_rejected = SimpleNamespace(
        idempotency_key=f"monthly-sov-remasure:{hospital_id}:2026-08:stall-unlock",
        request_payload={},
        state=OperationRunState.FAILED,
        safe_error_code="BROKER_UNAVAILABLE",
    )

    async def prior_runs(_db, *_args):
        return [broker_rejected]

    async def progress(_db, *_args):
        return _progress(_auto_run(), hours_ago=1)

    monkeypatch.setattr(gate, "_prior_manual_remasure_runs", prior_runs)
    monkeypatch.setattr(gate, "load_monthly_recovery_progress", progress)

    with pytest.raises(gate.ManualRemasureLocked) as exc:
        await gate.authorize_manual_remasure(
            None, hospital_id=hospital_id, year=2026, month=8,
            request_fingerprint="first", now=NOW,
        )
    assert exc.value.decision.code == gate.LOCKED
