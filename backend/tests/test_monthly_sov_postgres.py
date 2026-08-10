"""Real PostgreSQL proof for fixed-manifest SoV persistence and API detail output."""

import os
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.admin.reports import _serialize
from app.models.admin_user import AdminUser
from app.models.hospital import Hospital
from app.models.report import MonthlyReport
from app.models.sov import AIQueryTarget, QueryMatrix, SovRecord
from app.services.monthly_manifest import (
    ManifestCellSpec,
    exclude_cell,
    freeze_monthly_manifest,
    link_attempt,
)
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_repository import load_monthly_sov_manifest

POSTGRES_URL = os.getenv(
    "TASK22_DATABASE_URL",
    "postgresql://reputation:reputation@localhost:5434/reputation_test",
)
pytestmark = pytest.mark.skipif(
    "TASK22_DATABASE_URL" not in os.environ,
    reason="set TASK22_DATABASE_URL to an isolated Alembic-head PostgreSQL database",
)


def test_migrated_postgres_cells_round_trip_to_persisted_summary_and_detail_api() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    with Session(engine) as db:
        hospital = Hospital(name="고정 측정표 테스트의원", slug=f"task22-{uuid.uuid4().hex}")
        owner = AdminUser(
            email=f"task22-{uuid.uuid4().hex}@example.com",
            name="측정표 테스트 관리자",
            role="OWNER",
            password_hash="not-used-in-test",
        )
        db.add_all((hospital, owner))
        db.flush()
        target = AIQueryTarget(
            hospital_id=hospital.id,
            name="강남 병원 찾기",
            target_intent="LOCAL",
            platforms=["chatgpt", "gemini"],
        )
        local_query = QueryMatrix(
            hospital_id=hospital.id, query_text="강남에서 내과 찾아줘", query_intent="LOCAL"
        )
        info_query = QueryMatrix(
            hospital_id=hospital.id, query_text="내과 진료가 뭐야", query_intent="INFO"
        )
        db.add_all((target, local_query, info_query))
        db.flush()
        manifest = freeze_monthly_manifest(
            db,
            hospital.id,
            2026,
            8,
            [
                ManifestCellSpec(
                    query_key=f"query:{local_query.id}",
                    query_text=local_query.query_text,
                    platform="chatgpt",
                    query_matrix_id=local_query.id,
                    query_target_id=target.id,
                    query_variant_id=None,
                    query_intent="LOCAL",
                ),
                ManifestCellSpec(
                    query_key=f"query:{info_query.id}",
                    query_text=info_query.query_text,
                    platform="chatgpt",
                    query_matrix_id=info_query.id,
                    query_target_id=target.id,
                    query_variant_id=None,
                    query_intent="INFO",
                ),
            ],
            gemini_configured=True,
        )
        cells = {(cell.query_key, cell.platform): cell for cell in manifest.cells}
        local_chatgpt = cells[(f"query:{local_query.id}", "chatgpt")]
        info_gemini = cells[(f"query:{info_query.id}", "gemini")]
        records = (
            SovRecord(
                hospital_id=hospital.id,
                query_id=local_query.id,
                ai_query_target_id=target.id,
                ai_platform="chatgpt",
                is_mentioned=True,
                raw_response="테스트의원 언급",
                measurement_status="SUCCESS",
            ),
            SovRecord(
                hospital_id=hospital.id,
                query_id=info_query.id,
                ai_query_target_id=target.id,
                ai_platform="gemini",
                is_mentioned=False,
                raw_response="일반 정보",
                measurement_status="SUCCESS",
            ),
        )
        db.add_all(records)
        db.flush()
        db.add_all((link_attempt(local_chatgpt, records[0]), link_attempt(info_gemini, records[1])))
        exclude_cell(
            cells[(f"query:{info_query.id}", "chatgpt")],
            role="OWNER",
            reason="LEGAL_REMOVAL",
            actor_id=owner.id,
        )
        local_query.query_intent = "INFO"
        db.flush()

        loaded = load_monthly_sov_manifest(db, manifest)
        payload = build_monthly_sov(loaded.cells, tuple(manifest.configured_platforms)).to_payload()
        report = MonthlyReport(
            hospital_id=hospital.id,
            manifest_id=manifest.id,
            period_year=2026,
            period_month=8,
            report_type="MONTHLY",
            sov_summary=payload,
        )
        db.add(report)
        db.flush()
        persisted = db.scalar(select(MonthlyReport).where(MonthlyReport.id == report.id))

        assert persisted is not None
        assert loaded.cells[0].query_target_id == target.id
        assert next(cell for cell in loaded.cells if cell.query_matrix_id == local_query.id).query_intent == "LOCAL"
        assert persisted.sov_summary["planned_count"] == 3
        assert persisted.sov_summary["success_count"] == 2
        assert persisted.sov_summary["failed_count"] == 1
        assert persisted.sov_summary["excluded_count"] == 1
        assert len(persisted.sov_summary["cells"]) == 4
        assert {cell["state_label"] for cell in persisted.sov_summary["cells"]} == {
            "측정 완료",
            "측정 못함",
            "사전 제외",
        }
        assert sum(row["cell_count"] for row in persisted.sov_summary["platforms"]) == 4
        assert _serialize(persisted, full=True)["sov_summary"] == persisted.sov_summary
        assert _serialize(persisted)["sov_summary"] is None
        db.rollback()
    engine.dispose()
