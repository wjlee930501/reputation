"""Real PostgreSQL proof for fixed-manifest SoV persistence and period execution."""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.admin.reports import _serialize
from app.models.admin_user import AdminUser
from app.models.hospital import Hospital
from app.models.operations import OperationRun
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
from app.workers import tasks
from tests.db_env import require_db_url

# TASK22_DATABASE_URL must point at an isolated Alembic-head PostgreSQL database. It has
# no default: unset fails the test (read at test time, so collection never errors).


def test_migrated_postgres_cells_round_trip_to_persisted_summary_and_detail_api() -> None:
    engine = create_engine(require_db_url("TASK22_DATABASE_URL"), pool_pre_ping=True)
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
                    query_key=f"query:{local_query.id}",
                    query_text=local_query.query_text,
                    platform="gemini",
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
                ManifestCellSpec(
                    query_key=f"query:{info_query.id}",
                    query_text=info_query.query_text,
                    platform="gemini",
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


def test_concurrent_resume_uses_one_null_scoped_period_identity_and_one_live_lease() -> None:
    engine = create_engine(require_db_url("TASK22_DATABASE_URL"), pool_pre_ping=True)
    period_key = "2099-11"
    observed_at = datetime(2099, 11, 24, tzinfo=UTC)
    barrier = threading.Barrier(2)
    claims: list[tasks.MonthlySovPeriodClaim | None] = []

    def claim(owner: str) -> None:
        with Session(engine) as db:
            barrier.wait()
            claims.append(
                tasks._claim_monthly_sov_period_run(
                    db, period_key, owner=owner, observed_at=observed_at
                )
            )

    workers = [threading.Thread(target=claim, args=(f"celery-{index}",)) for index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    with Session(engine) as db:
        rows = db.scalars(
            select(OperationRun).where(
                OperationRun.operation_type == tasks.MONTHLY_SOV_PERIOD_OPERATION,
                OperationRun.idempotency_key == f"monthly-sov-period:{period_key}",
            )
        ).all()
        assert len(rows) == 1
        assert [claim is not None for claim in claims].count(True) == 1
        winner = next(claim for claim in claims if claim is not None)
        assert winner is not None
        assert tasks._claim_monthly_sov_period_run(
            db, period_key, owner="new-task-id", observed_at=observed_at + timedelta(minutes=1)
        ) is None
        cursor_hospital_id, cursor_slot_id = uuid.uuid4(), uuid.uuid4()
        advanced = tasks._advance_monthly_sov_period_run(
            db,
            winner,
            hospital_id=cursor_hospital_id,
            slot_id=cursor_slot_id,
            dispatched=True,
            observed_at=observed_at,
        )
        assert advanced is not None
        assert tasks._release_monthly_sov_period_run(
            db, advanced, observed_at=observed_at
        )
        resumed = tasks._claim_monthly_sov_period_run(
            db, period_key, owner="new-task-id", observed_at=observed_at + timedelta(minutes=1)
        )
        assert resumed is not None
        assert resumed.run_id == rows[0].id
        assert resumed.cursor_hospital_id == cursor_hospital_id
        assert resumed.cursor_slot_id == cursor_slot_id
        db.execute(OperationRun.__table__.delete().where(OperationRun.id == rows[0].id))
        db.commit()
    engine.dispose()


def test_partial_budget_page_selects_at_most_ten_pending_stages() -> None:
    cell_a, cell_b = uuid.uuid4(), uuid.uuid4()
    terminal = SimpleNamespace(
        id=uuid.uuid4(),
        answer_status="RECEIVED",
        judgment_status="CONFIRMED",
        answer_attempt_count=1,
        judgment_attempt_count=1,
    )
    pending = [
        SimpleNamespace(
            id=uuid.UUID(int=index + 1),
            answer_status="PENDING",
            judgment_status="PENDING",
            answer_attempt_count=0,
            judgment_attempt_count=0,
        )
        for index in range(14)
    ]

    page = tasks._bounded_monthly_slot_page(
        {cell_a: [terminal, *pending[:7]], cell_b: pending[7:]}
    )

    selected_pending = [
        slot for slots in page.values() for slot in slots if not tasks.slot_is_terminal(slot)
    ]
    assert len(selected_pending) == 10
    assert terminal in page[cell_a]


def test_horizon_closes_zero_question_period_as_unavailable_with_truthful_null_sample() -> None:
    engine = create_engine(require_db_url("TASK22_DATABASE_URL"), pool_pre_ping=True)
    period_key = "2099-10"
    opened_at = datetime(2099, 10, 24, tzinfo=UTC)
    deadline = tasks._monthly_sov_recovery_deadline(2099, 10)
    with Session(engine) as db:
        claim = tasks._claim_monthly_sov_period_run(
            db, period_key, owner="horizon-task", observed_at=opened_at
        )
        assert claim is not None
        assert tasks._release_monthly_sov_period_run(db, claim, observed_at=opened_at)

        assert tasks._finalize_monthly_sov_period_run(
            db, period_key, observed_at=deadline.astimezone(UTC)
        )
        run = db.scalar(
            select(OperationRun).where(
                OperationRun.operation_type == tasks.MONTHLY_SOV_PERIOD_OPERATION,
                OperationRun.idempotency_key == f"monthly-sov-period:{period_key}",
            )
        )
        assert run is not None
        assert run.state == tasks.OperationRunState.SUCCEEDED
        assert run.result_summary["measurement_horizon"] == {
            "status": "UNAVAILABLE",
            "planned_slots": 0,
            "received_answers": 0,
            "confirmed_slots": 0,
            "ambiguous_slots": 0,
            "answer_failed_slots": 0,
            "judgment_failed_slots": 0,
            "pending_slots": 0,
            "platforms": [],
        }
        db.delete(run)
        db.commit()
    engine.dispose()


def test_three_day_partial_budget_round_robin_runs_through_report_entrypoint(monkeypatch) -> None:
    engine = create_engine(require_db_url("TASK22_DATABASE_URL"), pool_pre_ping=True)
    period_key = "2099-09"
    published: list[tuple[str, str]] = []

    def apply_async(*, args, task_id, **_kwargs) -> None:
        published.append((args[0], task_id))

    monkeypatch.setattr(tasks.run_sov_for_hospital, "apply_async", apply_async)
    with Session(engine, expire_on_commit=False) as db:
        hospitals = [
            Hospital(name=f"라운드로빈-{index}", slug=f"round-robin-{uuid.uuid4().hex}")
            for index in range(3)
        ]
        db.add_all(hospitals)
        db.commit()
        hospital_ids = {hospital.id for hospital in hospitals}
        monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: Session(engine, expire_on_commit=False))
        monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(tasks, "eligible_hospital_ids", lambda *_args: list(hospital_ids))
        monkeypatch.setattr(tasks, "_hospital_requires_monthly_sov_success", lambda *_args: True)
        monkeypatch.setattr(tasks, "_monthly_sov_measurement_succeeded", lambda *_args: False)
        monkeypatch.setattr(tasks, "_start_monthly_report_batch_run", lambda *_args: None)
        monkeypatch.setattr(tasks, "_finish_monthly_report_batch_run", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(tasks, "_record_weekly_sov_failure", lambda *_args, **_kwargs: None)
        clock = {"day": 1}
        monkeypatch.setattr(
            tasks.arrow,
            "now",
            lambda *_args, **_kwargs: tasks.arrow.get(
                2099, 10, clock["day"], 1, 0, tzinfo="Asia/Seoul"
            ),
        )
        for day in range(1, 4):
            clock["day"] = day
            result = tasks.run_monthly_reports.run()
            assert result == {
                "status": "PARTIAL",
                "total_count": len(hospitals),
                "success_count": 0,
                "failure_count": len(hospitals),
            }
            assert len(published) == day * len(hospitals)
            if day < 3:
                db.expire_all()
                runs = db.scalars(
                    select(OperationRun).where(
                        OperationRun.hospital_id.in_(hospital_ids),
                        OperationRun.operation_type == "RUN_SOV",
                        OperationRun.idempotency_key.like("monthly-sov:%:2099-09"),
                    )
                ).all()
                assert len(runs) == len(hospitals)
                for run in runs:
                    run.state = tasks.OperationRunState.FAILED
                    run.safe_error_code = "MONTHLY_SOV_COST_GUARD_BLOCKED"
                    run.version += 1
                db.commit()

        paid_dispatches_before_close = len(published)
        clock["day"] = 8
        close_result = tasks.run_monthly_reports.run()
        assert close_result["status"] == "PARTIAL"
        assert len(published) == paid_dispatches_before_close

        db.expire_all()
        period_runs = db.scalars(
            select(OperationRun).where(
                OperationRun.operation_type == tasks.MONTHLY_SOV_PERIOD_OPERATION,
                OperationRun.idempotency_key == f"monthly-sov-period:{period_key}",
            )
        ).all()
        assert len(period_runs) == 1
        assert period_runs[0].state == tasks.OperationRunState.SUCCEEDED
        assert period_runs[0].total_count == 9
        assert period_runs[0].result_summary["measurement_horizon"]["status"] == (
            "UNAVAILABLE"
        )
        assert period_runs[0].result_summary["monthly_measurement_cursor"][
            "hospital_id"
        ] in {str(hospital_id) for hospital_id in hospital_ids}
        db.execute(
            OperationRun.__table__.delete().where(
                (OperationRun.hospital_id.in_(hospital_ids))
                | (OperationRun.id == period_runs[0].id)
            )
        )
        db.execute(Hospital.__table__.delete().where(Hospital.id.in_(hospital_ids)))
        db.commit()
    engine.dispose()
