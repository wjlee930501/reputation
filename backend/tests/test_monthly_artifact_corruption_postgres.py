"""Corrupted artifact metadata must fail closed without stopping reconciliation."""

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyReportArtifact
from app.models.operations import Incident, NotificationOutbox
from app.models.report import MonthlyReport
from app.workers import monthly_artifact_incident_control, monthly_artifact_reconciliation

_POSTGRES_URL = os.getenv("TASK24_DATABASE_URL", "postgresql://reputation:reputation@localhost:5434/reputation_test")


def test_scalar_list_and_nonnumeric_metadata_open_once_without_crashing(
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
    hospital_ids = [uuid.uuid4() for _ in range(3)]
    monkeypatch.setattr(
        monthly_artifact_incident_control, "get_async_sessionmaker", lambda: async_sessions
    )

    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(monthly_artifact_reconciliation, "SyncSessionLocal", SessionContext)
    metadata_values: list[object] = [
        "손상된 문자열",
        ["손상된", "목록"],
        {
            "validation_version": "doctor-pdf-v1",
            "validation_source": "SYSTEM",
            "page_count": 1,
            "page_size": "A4",
            "glyph_count": "숫자 아님",
            "font_family": "Pretendard",
            "font_embedded": True,
            "korean_to_unicode": True,
            "link_count": "숫자 아님",
            "expected_link_present": True,
            "required_text_present": True,
            "sha256": "c" * 64,
            "byte_size": "숫자 아님",
        },
    ]

    try:
        for index, (hospital_id, metadata) in enumerate(
            zip(hospital_ids, metadata_values, strict=True)
        ):
            hospital = Hospital(
                id=hospital_id,
                name=f"손상 검증 정보 의원 {index}",
                slug=f"artifact-corrupt-{uuid.uuid4().hex}",
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
                doctor_pdf_path=f"gs://private/corrupt-{index}.pdf",
            )
            session.add_all((hospital, report))
            session.flush()
            system_source = index == 2
            session.add(
                MonthlyReportArtifact(
                    report_id=report.id,
                    audience="DOCTOR",
                    path=report.doctor_pdf_path,
                    sha256="c" * 64,
                    byte_size=4096,
                    validated=system_source,
                    validated_at=datetime.now(timezone.utc) if system_source else None,
                    validation_metadata=metadata,
                )
            )
        session.commit()

        first = monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents.run()
        session.expire_all()
        incidents = list(
            session.scalars(select(Incident).where(Incident.hospital_id.in_(hospital_ids)))
        )
        notices = list(
            session.scalars(
                select(NotificationOutbox).where(NotificationOutbox.hospital_id.in_(hospital_ids))
            )
        )

        assert first == {"status": "completed", "opened_count": 3, "recovered_count": 0}
        assert len(incidents) == 3
        assert all(incident.state == "OPEN" for incident in incidents)
        assert all(
            incident.safe_error_code == "DOCTOR_PDF_ARTIFACT_INVALID"
            for incident in incidents
        )
        assert len(notices) == 3
        assert all(notice.notification_type == "INCIDENT_OPEN" for notice in notices)

        replay = monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents.run()
        assert replay == {"status": "completed", "opened_count": 0, "recovered_count": 0}
        session.expire_all()
        assert len(
            list(
                session.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.hospital_id.in_(hospital_ids)
                    )
                )
            )
        ) == 3
    finally:
        session.rollback()
        session.execute(
            delete(NotificationOutbox).where(NotificationOutbox.hospital_id.in_(hospital_ids))
        )
        session.execute(delete(Incident).where(Incident.hospital_id.in_(hospital_ids)))
        report_ids = select(MonthlyReport.id).where(MonthlyReport.hospital_id.in_(hospital_ids))
        session.execute(
            delete(MonthlyReportArtifact).where(MonthlyReportArtifact.report_id.in_(report_ids))
        )
        session.execute(delete(MonthlyReport).where(MonthlyReport.hospital_id.in_(hospital_ids)))
        session.execute(delete(Hospital).where(Hospital.id.in_(hospital_ids)))
        session.commit()
        session.close()
        asyncio.run(async_engine.dispose())
        engine.dispose()
