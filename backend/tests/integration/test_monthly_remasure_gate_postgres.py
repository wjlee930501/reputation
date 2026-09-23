"""Manual monthly remasure gate reads real manifest progress and spends one allowance."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyMeasurementCell, MonthlyMeasurementManifest
from app.models.operations import OperationRun, OperationRunState
from app.models.sov import MeasurementRun, QueryMatrix
from app.services import monthly_remasure_gate as gate
from app.services.measurement_slots import ensure_monthly_slots

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


async def _seed(session, *, last_progress_at: datetime):
    hospital = Hospital(name="재측정 잠금 의원", slug=f"remasure-gate-{uuid.uuid4().hex[:12]}")
    session.add(hospital)
    await session.flush()
    query = QueryMatrix(hospital_id=hospital.id, query_text="강남 내과 추천", query_intent="LOCAL")
    run = MeasurementRun(hospital_id=hospital.id, run_label="remasure-gate-test")
    manifest = MonthlyMeasurementManifest(
        hospital_id=hospital.id,
        period_year=2026,
        period_month=8,
        configured_platforms=["chatgpt"],
        platform_provenance={"observation_slots": {"version": 1, "repeat_count": 2}},
        frozen_at=NOW - timedelta(days=3),
        closes_at=NOW + timedelta(days=4),
    )
    session.add_all([query, run, manifest])
    await session.flush()
    cell = MonthlyMeasurementCell(
        manifest_id=manifest.id,
        query_matrix_id=query.id,
        query_key=f"query:{query.id}",
        query_text=query.query_text,
        platform="chatgpt",
    )
    session.add(cell)
    await session.flush()
    slots = await session.run_sync(
        lambda sync: ensure_monthly_slots(
            sync,
            cell=cell,
            hospital_id=hospital.id,
            measurement_run_id=run.id,
            repeat_count=2,
            protocol={"test": "remasure-gate"},
        )
    )
    slots[0].answer_status = "RECEIVED"
    slots[0].raw_response = "답변"
    slots[0].answered_at = last_progress_at
    # A fresh lease claim on the pending repeat is activity, not progress.
    slots[1].answer_attempt_count = 1
    slots[1].updated_at = NOW - timedelta(minutes=10)
    session.add(
        OperationRun(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            operation_type="RUN_SOV",
            state=OperationRunState.QUEUED,
            idempotency_key=gate.automatic_monthly_sov_key(hospital.id, 2026, 8),
            task_id=str(uuid.uuid4()),
            attempt_count=0,
            total_count=1,
            success_count=0,
            failure_count=0,
            skipped_count=0,
            request_payload={},
            result_summary={"measurement_month": "2026-08", "measurement_mode": "monthly"},
            version=1,
        )
    )
    await session.flush()
    return hospital


async def test_recent_manifest_progress_keeps_remasure_locked(pg_async_session):
    hospital = await _seed(pg_async_session, last_progress_at=NOW - timedelta(hours=3))

    progress = await gate.load_monthly_recovery_progress(pg_async_session, hospital.id, 2026, 8)
    assert progress.remaining_work == 2
    assert progress.last_progress_at == NOW - timedelta(hours=3)

    with pytest.raises(gate.ManualRemasureLocked) as exc:
        await gate.authorize_manual_remasure(
            pg_async_session,
            hospital_id=hospital.id,
            year=2026,
            month=8,
            request_fingerprint="click-1",
            now=NOW,
        )
    assert exc.value.decision.code == gate.LOCKED


async def test_stalled_manifest_unlocks_once(pg_async_session):
    hospital = await _seed(pg_async_session, last_progress_at=NOW - timedelta(hours=13))

    allowed = await gate.authorize_manual_remasure(
        pg_async_session,
        hospital_id=hospital.id,
        year=2026,
        month=8,
        request_fingerprint="click-1",
        now=NOW,
    )
    assert allowed.operation_key.endswith(":2026-08:stall-unlock")

    pg_async_session.add(
        OperationRun(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            operation_type="RUN_SOV",
            state=OperationRunState.QUEUED,
            idempotency_key=allowed.operation_key,
            task_id=str(uuid.uuid4()),
            attempt_count=0,
            total_count=0,
            success_count=0,
            failure_count=0,
            skipped_count=0,
            request_payload=allowed.request_payload_extra,
            version=1,
        )
    )
    await pg_async_session.flush()

    with pytest.raises(gate.ManualRemasureLocked) as exc:
        await gate.authorize_manual_remasure(
            pg_async_session,
            hospital_id=hospital.id,
            year=2026,
            month=8,
            request_fingerprint="click-2",
            now=NOW,
        )
    assert exc.value.decision.code == gate.ALREADY_USED


def _manual_run(hospital_id, key, payload, *, state, error_code=None):
    return OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="RUN_SOV",
        state=state,
        idempotency_key=key,
        task_id=str(uuid.uuid4()),
        attempt_count=0,
        total_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=payload,
        safe_error_code=error_code,
        version=1,
    )


async def test_broker_rejected_unlock_retries_under_next_key(pg_async_session):
    hospital = await _seed(pg_async_session, last_progress_at=NOW - timedelta(hours=13))
    first = await gate.authorize_manual_remasure(
        pg_async_session,
        hospital_id=hospital.id,
        year=2026,
        month=8,
        request_fingerprint="click-1",
        now=NOW,
    )
    pg_async_session.add(
        _manual_run(
            hospital.id,
            first.operation_key,
            first.request_payload_extra,
            state=OperationRunState.FAILED,
            error_code="BROKER_UNAVAILABLE",
        )
    )
    await pg_async_session.flush()

    retried = await gate.authorize_manual_remasure(
        pg_async_session,
        hospital_id=hospital.id,
        year=2026,
        month=8,
        request_fingerprint="click-1",
        now=NOW,
    )
    assert retried.operation_key == f"{first.operation_key}:2"
    # The unique idempotency index accepts the retry key next to the dead run.
    pg_async_session.add(
        _manual_run(
            hospital.id,
            retried.operation_key,
            retried.request_payload_extra,
            state=OperationRunState.QUEUED,
        )
    )
    await pg_async_session.flush()

    with pytest.raises(gate.ManualRemasureLocked) as exc:
        await gate.authorize_manual_remasure(
            pg_async_session,
            hospital_id=hospital.id,
            year=2026,
            month=8,
            request_fingerprint="click-2",
            now=NOW,
        )
    assert exc.value.decision.code == gate.ALREADY_USED
