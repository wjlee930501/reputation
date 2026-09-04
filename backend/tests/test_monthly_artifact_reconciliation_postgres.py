"""A worker crash after report commit is repaired from durable database truth."""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from monthly_artifact_test_support import published
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyReportArtifact
from app.models.operations import Incident, NotificationOutbox, OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.services.report_artifact_validation import DoctorPdfValidationError
from app.workers import (
    monthly_artifact_incident_control,
    monthly_artifact_reconciliation,
    monthly_artifact_recovery_control,
)
from app.workers.monthly_artifact_incident_contracts import MonthlyArtifactIncidentContext

_POSTGRES_URL = os.getenv(
    "TASK24_DATABASE_URL",
    "postgresql://reputation:reputation@localhost:5434/reputation_test",
)


def test_committed_blocked_report_repairs_missing_incident_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(_POSTGRES_URL, future=True)
    try:
        connection = engine.connect()
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")
    connection.close()
    session = Session(engine, expire_on_commit=False)
    async_url = _POSTGRES_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    async_engine = create_async_engine(async_url, poolclass=NullPool)
    async_sessions = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    invalid_hospital_id = uuid.uuid4()
    invalid_report_id = uuid.uuid4()
    run_id = uuid.uuid4()
    wrong_period_run_id = uuid.uuid4()
    invalid_run_id = uuid.uuid4()
    monkeypatch.setattr(
        monthly_artifact_incident_control, "get_async_sessionmaker", lambda: async_sessions
    )

    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(monthly_artifact_reconciliation, "SyncSessionLocal", SessionContext)

    try:
        hospital = Hospital(
            id=hospital_id,
            name="누락 이슈 자동 복구 의원",
            slug=f"artifact-reconcile-{uuid.uuid4().hex}",
        )
        report = MonthlyReport(
            hospital_id=hospital_id,
            period_year=2026,
            period_month=7,
            report_type="MONTHLY",
            version=1,
            quality="COMPLETE",
            planned_count=20,
            success_count=20,
            failed_count=0,
            doctor_pdf_path=None,
            delivery_blockers=["DOCTOR_ARTIFACT_UNVALIDATED"],
        )
        run = OperationRun(
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
            result_summary={"stage": "RUNNING", "period_year": 2026, "period_month": 7},
            created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        wrong_period_run = OperationRun(
            id=wrong_period_run_id,
            hospital_id=hospital_id,
            operation_type="GENERATE_MONTHLY_REPORT",
            state=OperationRunState.RUNNING,
            attempt_count=1,
            total_count=1,
            success_count=0,
            failure_count=0,
            skipped_count=0,
            request_payload={"source_type": "hospital", "source_id": str(hospital_id)},
            result_summary={"stage": "RUNNING", "period_year": 2026, "period_month": 6},
            created_at=datetime.now(timezone.utc),
        )
        invalid_hospital = Hospital(
            id=invalid_hospital_id,
            name="검증 정보 불일치 의원",
            slug=f"artifact-invalid-{uuid.uuid4().hex}",
        )
        invalid_report = MonthlyReport(
            id=invalid_report_id,
            hospital_id=invalid_hospital_id,
            period_year=2026,
            period_month=7,
            report_type="MONTHLY",
            version=1,
            quality="COMPLETE",
            planned_count=20,
            success_count=20,
            failed_count=0,
            doctor_pdf_path="gs://private/missing-artifact.pdf",
            delivery_blockers=[],
        )
        invalid_run = OperationRun(
            id=invalid_run_id,
            hospital_id=invalid_hospital_id,
            operation_type="GENERATE_MONTHLY_REPORT",
            state=OperationRunState.PARTIAL,
            attempt_count=1,
            total_count=1,
            success_count=0,
            failure_count=1,
            skipped_count=0,
            request_payload={"source_type": "hospital", "source_id": str(invalid_hospital_id)},
            result_summary={
                "stage": "BLOCKED",
                "period_year": 2026,
                "period_month": 7,
                "report_id": str(invalid_report_id),
            },
        )
        session.add_all(
            (
                hospital,
                report,
                run,
                wrong_period_run,
                invalid_hospital,
                invalid_report,
                invalid_run,
            )
        )
        session.commit()
        assert session.scalar(select(Incident).where(Incident.hospital_id == hospital_id)) is None

        first = monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents.run()
        session.expire_all()
        incidents = {
            item.hospital_id: item
            for item in session.scalars(
                select(Incident).where(
                    Incident.hospital_id.in_((hospital_id, invalid_hospital_id))
                )
            )
        }
        incident = incidents[hospital_id]
        notices = list(
            session.scalars(
                select(NotificationOutbox).where(
                    NotificationOutbox.hospital_id.in_((hospital_id, invalid_hospital_id))
                )
            )
        )

        assert first == {
            "status": "completed",
            "opened_count": 2,
            "recovered_count": 0,
        }
        assert incident is not None and incident.state == "OPEN"
        assert incident.operation_run_id == run_id
        assert incident.safe_error_code == "DOCTOR_PDF_INCIDENT_RECONCILED"
        assert incidents[invalid_hospital_id].state == "OPEN"
        assert incidents[invalid_hospital_id].operation_run_id == invalid_run_id
        assert incidents[invalid_hospital_id].safe_error_code == "DOCTOR_PDF_ARTIFACT_INVALID"
        assert len(notices) == 2
        assert all(notice.notification_type == "INCIDENT_OPEN" for notice in notices)
        assert all("무슨 문제인지" in str(notice.payload) for notice in notices)
        assert all("고객 영향" in str(notice.payload) for notice in notices)
        assert all("지금 할 일" in str(notice.payload) for notice in notices)

        second = monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents.run()
        session.expire_all()
        assert second == {
            "status": "completed",
            "opened_count": 0,
            "recovered_count": 0,
        }
        assert len(
            list(
                session.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.hospital_id.in_(
                            (hospital_id, invalid_hospital_id)
                        )
                    )
                )
            )
        ) == 2
    finally:
        session.rollback()
        session.execute(
            delete(NotificationOutbox).where(
                NotificationOutbox.hospital_id.in_((hospital_id, invalid_hospital_id))
            )
        )
        session.execute(
            delete(Incident).where(Incident.hospital_id.in_((hospital_id, invalid_hospital_id)))
        )
        session.execute(
            delete(MonthlyReport).where(
                MonthlyReport.hospital_id.in_((hospital_id, invalid_hospital_id))
            )
        )
        session.execute(
            delete(OperationRun).where(
                OperationRun.id.in_((run_id, wrong_period_run_id, invalid_run_id))
            )
        )
        session.execute(
            delete(Hospital).where(Hospital.id.in_((hospital_id, invalid_hospital_id)))
        )
        session.commit()
        session.close()
        asyncio.run(async_engine.dispose())
        engine.dispose()


def test_valid_two_page_artifact_recovers_false_invalid_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(_POSTGRES_URL, future=True)
    try:
        connection = engine.connect()
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")
    connection.close()
    session = Session(engine, expire_on_commit=False)
    async_url = _POSTGRES_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    async_engine = create_async_engine(async_url, poolclass=NullPool)
    async_sessions = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    report_id = uuid.uuid4()
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

    try:
        hospital = Hospital(
            id=hospital_id,
            name="2쪽 원장 PDF 자동 복구 의원",
            slug=f"artifact-two-page-{uuid.uuid4().hex}",
        )
        report = MonthlyReport(
            id=report_id,
            hospital_id=hospital_id,
            period_year=2026,
            period_month=8,
            report_type="MONTHLY",
            version=1,
            quality="COMPLETE",
            planned_count=20,
            success_count=20,
            failed_count=0,
            delivery_blockers=[],
        )
        artifact_value = published(report_id, page_count=2)
        report.doctor_pdf_path = artifact_value.path
        artifact = MonthlyReportArtifact(
            report_id=report_id,
            audience="DOCTOR",
            path=artifact_value.path,
            sha256=artifact_value.sha256,
            byte_size=artifact_value.byte_size,
            validated=True,
            validated_at=datetime.now(timezone.utc),
            validation_metadata=artifact_value.metadata.model_dump(mode="json"),
        )
        session.add_all((hospital, report))
        session.flush()
        session.add(artifact)
        session.commit()
        context = MonthlyArtifactIncidentContext(
            hospital_id=hospital_id,
            hospital_name=hospital.name,
            report_id=report_id,
            year=2026,
            month=8,
            operation_run_id=None,
        )
        asyncio.run(
            monthly_artifact_incident_control.record_monthly_artifact_failure(
                context,
                DoctorPdfValidationError(
                    "DOCTOR_PDF_ARTIFACT_INVALID",
                    "기존 조정기가 2쪽 PDF를 잘못 판정했습니다.",
                ),
            )
        )

        result = monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents.run()
        session.expire_all()
        incident = session.scalar(select(Incident).where(Incident.hospital_id == hospital_id))
        notices = list(
            session.scalars(
                select(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
            )
        )

        assert result == {"status": "completed", "opened_count": 0, "recovered_count": 1}
        assert incident is not None and incident.state == "RECOVERED"
        assert [notice.notification_type for notice in notices] == [
            "INCIDENT_OPEN",
            "INCIDENT_RECOVERED",
        ]
        assert monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents.run() == {
            "status": "completed",
            "opened_count": 0,
            "recovered_count": 0,
        }
    finally:
        session.rollback()
        session.execute(
            delete(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
        )
        session.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
        session.execute(
            delete(MonthlyReportArtifact).where(MonthlyReportArtifact.report_id == report_id)
        )
        session.execute(delete(MonthlyReport).where(MonthlyReport.id == report_id))
        session.execute(delete(Hospital).where(Hospital.id == hospital_id))
        session.commit()
        session.close()
        asyncio.run(async_engine.dispose())
        engine.dispose()
