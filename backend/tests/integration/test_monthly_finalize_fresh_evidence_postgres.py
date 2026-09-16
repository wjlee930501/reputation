"""The first monthly pass must finalize its committed evidence, not an ORM cache."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyMeasurementCell, MonthlyMeasurementManifest
from app.models.sov import MeasurementRun, QueryMatrix, SovRecord
from app.services.measurement_slots import ensure_monthly_slots
from app.services.monthly_manifest import link_attempt
from app.workers import tasks


def test_first_pass_finalizes_after_preloading_empty_slot_collection(pg_conn, monkeypatch):
    now = datetime.now(UTC)
    with Session(
        bind=pg_conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as db:
        hospital = Hospital(name="Finalization evidence", slug=f"finalize-{uuid.uuid4()}")
        db.add(hospital)
        db.flush()
        query = QueryMatrix(hospital_id=hospital.id, query_text="검증 질문", query_intent="LOCAL")
        run = MeasurementRun(hospital_id=hospital.id, run_label="finalization-test")
        manifest = MonthlyMeasurementManifest(
            hospital_id=hospital.id,
            period_year=now.year,
            period_month=now.month,
            configured_platforms=["chatgpt"],
            platform_provenance={"observation_slots": {"version": 1, "repeat_count": 1}},
            closes_at=now + timedelta(days=30),
        )
        db.add_all([query, run, manifest])
        db.flush()
        cell = MonthlyMeasurementCell(
            manifest_id=manifest.id,
            query_matrix_id=query.id,
            query_key=f"query:{query.id}",
            query_text=query.query_text,
            platform="chatgpt",
        )
        db.add(cell)
        db.commit()
        assert manifest.cells == [cell]
        assert cell.attempts == [] and cell.observation_slots == []
        slots = ensure_monthly_slots(
            db,
            cell=cell,
            hospital_id=hospital.id,
            measurement_run_id=run.id,
            repeat_count=1,
            protocol={"test": "finalization"},
        )
        record = SovRecord(
            hospital_id=hospital.id,
            query_id=query.id,
            measurement_run_id=run.id,
            ai_platform="chatgpt",
            is_mentioned=True,
            mention_verdict="MATCHED",
            raw_response="검증 답변",
            measurement_status="SUCCESS",
        )
        db.add(record)
        db.flush()
        slots[0].answer_status = "RECEIVED"
        slots[0].judgment_status = "CONFIRMED"
        slots[0].raw_response = record.raw_response
        slots[0].sov_record_id = record.id
        slots[0].completed_at = now
        cell.state = "SUCCESS"
        db.add(link_attempt(cell, record))
        db.commit()
        assert len(cell.attempts) == 1  # links are persisted before first-pass finalization
        errors = []
        monkeypatch.setattr(tasks, "_record_weekly_sov_failure", lambda *a, **k: errors.append(a))
        monkeypatch.setattr(tasks, "_finish_sov_operation_run", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "is_monthly_recovery_window", lambda *a, **k: False)
        task = SimpleNamespace(request=SimpleNamespace(headers={}))
        assert (
            tasks._complete_monthly_measurement_and_dispatch_report(
                db, task, hospital, manifest, now.year, now.month
            )
            is True
        )
        assert not errors
        assert (
            tasks._manifest_observation_adequacy(manifest, deadline_reached=True).status
            == "COMPLETE"
        )
