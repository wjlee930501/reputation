"""Real persisted repeats must agree with customer headline and evidence text."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.hospital import Hospital
from app.models.sov import MeasurementRun, QueryMatrix, SovRecord
from app.services.measurement_slots import ensure_monthly_slots
from app.services.monthly_manifest import ManifestCellSpec, freeze_monthly_manifest, link_attempt
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_repository import load_monthly_sov_manifest
from app.services.report_engine import _query_text_of


def test_persisted_canonical_repeat_and_frozen_text_win_without_writing_live_query(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as db:
        hospital = Hospital(name="월간 집계 검증 의원", slug="report-sample-" + uuid.uuid4().hex)
        db.add(hospital)
        db.flush()
        query = QueryMatrix(
            hospital_id=hospital.id, query_text="측정 당시의 병원 질문", query_intent="LOCAL"
        )
        run = MeasurementRun(
            hospital_id=hospital.id, run_label="isolated report test", status="RUNNING", config={}
        )
        db.add_all([query, run])
        db.flush()
        manifest = freeze_monthly_manifest(
            db,
            hospital.id,
            2026,
            8,
            [
                ManifestCellSpec(
                    query_key=f"query:{query.id}",
                    query_text=query.query_text,
                    platform="chatgpt",
                    query_matrix_id=query.id,
                    query_target_id=None,
                    query_variant_id=None,
                    query_intent="LOCAL",
                )
            ],
            gemini_configured=False,
        )
        cell = manifest.cells[0]
        slot = ensure_monthly_slots(
            db,
            cell=cell,
            hospital_id=hospital.id,
            measurement_run_id=run.id,
            repeat_count=1,
            protocol={"test": "snapshot"},
        )[0]
        records = []
        for mentioned in (True, False):
            record = SovRecord(
                hospital_id=hospital.id,
                query_id=query.id,
                ai_platform="chatgpt",
                measured_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
                is_mentioned=mentioned,
                raw_response="이전 재시도" if mentioned else "최종 확정 답변",
                measurement_status="SUCCESS",
            )
            db.add(record)
            db.flush()
            db.add(link_attempt(cell, record))
            records.append(record)
        slot.answer_status, slot.judgment_status = "RECEIVED", "CONFIRMED"
        slot.sov_record_id = records[-1].id
        query.query_text = "현재는 다른 질문으로 변경됨"
        db.flush()
        assert not db.dirty
        loaded = load_monthly_sov_manifest(db, manifest)
        summary = build_monthly_sov(loaded.cells, tuple(manifest.configured_platforms))
        assert summary.sov_pct == 0.0 and summary.attempts_used == 1
        assert [record.id for record in loaded.scored_records] == [records[-1].id]
        assert _query_text_of(loaded.scored_records[0]) == "측정 당시의 병원 질문"
        assert query.query_text == "현재는 다른 질문으로 변경됨"
        assert not db.dirty
        db.rollback()
