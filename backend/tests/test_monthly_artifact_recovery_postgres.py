"""월간 원장용 PDF 실패 뒤 재생성 복구를 실제 PostgreSQL로 검증한다."""

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import arrow
import pytest
from monthly_artifact_test_support import apply_complete, monthly_sov, published
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyMeasurementManifest, MonthlyReportArtifact
from app.models.operations import Incident, NotificationOutbox, OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.services.report_artifact_validation import DoctorPdfValidationError
from app.workers import (
    monthly_artifact_incident_control,
    monthly_artifact_reconciliation,
    monthly_artifact_recovery_control,
    tasks,
)

_POSTGRES_URL = os.getenv("TASK24_DATABASE_URL", "postgresql://reputation:reputation@localhost:5434/reputation_test")


def test_failed_v1_then_valid_v2_recovers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(_POSTGRES_URL, future=True)
    try:
        connection = engine.connect()
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")
    connection.close()
    session = Session(engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    run_ids = (uuid.uuid4(), uuid.uuid4())
    async_url = _POSTGRES_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    async_engine = create_async_engine(async_url, poolclass=NullPool)
    async_sessions = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(
        monthly_artifact_incident_control, "get_async_sessionmaker", lambda: async_sessions
    )
    monkeypatch.setattr(
        monthly_artifact_recovery_control, "get_async_sessionmaker", lambda: async_sessions
    )

    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(monthly_artifact_reconciliation, "SyncSessionLocal", SessionContext)
    monkeypatch.setattr(
        tasks,
        "load_monthly_sov_manifest",
        lambda *_args: SimpleNamespace(cells=(), selected_records=(), scored_records=()),
    )
    monkeypatch.setattr(tasks, "build_monthly_sov", lambda *_args, **_kwargs: monthly_sov())
    monkeypatch.setattr(tasks, "generate_pdf_report", lambda **_kwargs: "gs://qa-private/ae.pdf")
    monkeypatch.setattr(tasks, "build_content_attribution_summary", lambda *_args: {})
    monkeypatch.setattr(tasks, "build_monthly_essence_summary", lambda *_args: {})
    monkeypatch.setattr(tasks, "apply_manifest_to_report", apply_complete)
    calls = 0

    def publish(_hospital, report_id, _period_start, _view, _public_url):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DoctorPdfValidationError(
                "DOCTOR_PDF_STORAGE_FAILED",
                "원장 전달용 PDF 파일 업로드에 실패했습니다.",
            )
        return published(report_id)

    monkeypatch.setattr(tasks, "generate_doctor_pdf_report", publish)

    try:
        hospital = Hospital(
            id=hospital_id,
            name="원장 PDF 재생성 복구 의원",
            slug=f"doctor-recovery-{uuid.uuid4().hex}",
            plan="PLAN_16",
        )
        manifest = MonthlyMeasurementManifest(
            hospital_id=hospital_id,
            period_year=2026,
            period_month=7,
            configured_platforms=["chatgpt", "gemini"],
            platform_provenance={"query_intents": {}},
            closes_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            closed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        runs = [
            OperationRun(
                id=run_id,
                hospital_id=hospital_id,
                operation_type="GENERATE_MONTHLY_REPORT",
                state=OperationRunState.RUNNING,
                attempt_count=1,
                total_count=1,
                success_count=0,
                failure_count=0,
                skipped_count=0,
                request_payload={"source_type": "hospital", "source_id": str(hospital_id)},
            )
            for run_id in run_ids
        ]
        session.add_all((hospital, manifest, *runs))
        session.commit()
        anchor = arrow.get(2026, 7, 31, 23, 59, tzinfo="Asia/Seoul")

        first = tasks._build_monthly_report_for_hospital(
            session,
            hospital,
            anchor,
            rebuild=True,
            correlation_key=f"recovery-v1:{uuid.uuid4()}",
            operation_run_id=run_ids[0],
        )
        tasks._finish_monthly_operation_run(session, run_ids[0], hospital_id, 2026, 7, first)
        reports = list(
            session.scalars(
                select(MonthlyReport)
                .where(MonthlyReport.hospital_id == hospital_id)
                .order_by(MonthlyReport.version)
            )
        )
        assert first == "blocked_artifact"
        assert reports[0].doctor_pdf_path is None

        async def lose_recovery_projection(_context):
            return 0

        monkeypatch.setattr(tasks, "recover_monthly_artifact_failures", lose_recovery_projection)

        second = tasks._build_monthly_report_for_hospital(
            session,
            hospital,
            anchor,
            rebuild=True,
            correlation_key=f"recovery-v2:{uuid.uuid4()}",
            operation_run_id=run_ids[1],
        )
        tasks._finish_monthly_operation_run(session, run_ids[1], hospital_id, 2026, 7, second)
        session.expire_all()
        reports = list(
            session.scalars(
                select(MonthlyReport)
                .where(MonthlyReport.hospital_id == hospital_id)
                .order_by(MonthlyReport.version)
            )
        )
        incident = session.scalar(select(Incident).where(Incident.hospital_id == hospital_id))
        outboxes = list(
            session.scalars(
                select(NotificationOutbox)
                .where(NotificationOutbox.hospital_id == hospital_id)
                .order_by(NotificationOutbox.created_at)
            )
        )
        artifact = session.scalar(
            select(MonthlyReportArtifact).where(
                MonthlyReportArtifact.report_id == reports[1].id
            )
        )
        second_run = session.get(OperationRun, run_ids[1])

        assert second == "created"
        assert [(report.version, report.supersedes_report_id) for report in reports] == [
            (1, None),
            (2, reports[0].id),
        ]
        assert reports[0].doctor_pdf_path is None
        assert reports[1].doctor_pdf_path == f"gs://qa-private/monthly/{reports[1].id}.pdf"
        assert artifact is not None and artifact.sha256 == reports[1].id.hex * 2
        assert incident is not None and incident.state == "OPEN"
        assert [item.notification_type for item in outboxes] == ["INCIDENT_OPEN"]
        assert second_run is not None and second_run.state == OperationRunState.SUCCEEDED
        assert second_run.result_summary["stage"] == "ARTIFACT_VALIDATED"

        reconciled = monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents.run()
        assert reconciled == {"status": "completed", "opened_count": 0, "recovered_count": 1}
        session.expire_all()
        incident = session.scalar(select(Incident).where(Incident.hospital_id == hospital_id))
        assert incident is not None and incident.state == "RECOVERED"
        assert len(
            list(
                session.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.hospital_id == hospital_id
                    )
                )
            )
        ) == 2
        replayed = monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents.run()
        assert replayed == {"status": "completed", "opened_count": 0, "recovered_count": 0}
        session.expire_all()
        notices = list(
            session.scalars(
                select(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
            )
        )
        assert [notice.notification_type for notice in notices] == [
            "INCIDENT_OPEN",
            "INCIDENT_RECOVERED",
        ]
        assert notices[1].operation_run_id == run_ids[1]
    finally:
        session.rollback()
        session.execute(
            delete(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
        )
        session.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
        session.execute(
            delete(MonthlyReportArtifact).where(
                MonthlyReportArtifact.report_id.in_(
                    select(MonthlyReport.id).where(MonthlyReport.hospital_id == hospital_id)
                )
            )
        )
        reports = list(
            session.scalars(
                select(MonthlyReport)
                .where(MonthlyReport.hospital_id == hospital_id)
                .order_by(MonthlyReport.version.desc())
            )
        )
        for report in reports:
            session.delete(report)
            session.flush()
        session.execute(
            delete(MonthlyMeasurementManifest).where(
                MonthlyMeasurementManifest.hospital_id == hospital_id
            )
        )
        session.execute(delete(OperationRun).where(OperationRun.id.in_(run_ids)))
        session.execute(delete(Hospital).where(Hospital.id == hospital_id))
        session.commit()
        session.close()
        tasks._run_async(async_engine.dispose())
        engine.dispose()
