"""Real Alembic-PostgreSQL proofs for eligibility and report-version serialization."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import models
from app.services.monthly_period import (
    ReportBuildReason,
    eligible_hospital_ids,
    lock_report_version_plan,
    reporting_period,
)

KST = ZoneInfo("Asia/Seoul")


def _report(
    hospital_id: uuid.UUID, version: int, supersedes: uuid.UUID | None
) -> models.MonthlyReport:
    return models.MonthlyReport(
        hospital_id=hospital_id,
        period_year=2026,
        period_month=8,
        report_type="MONTHLY",
        version=version,
        supersedes_report_id=supersedes,
        quality="BLOCKED",
        planned_count=0,
        success_count=0,
        failed_count=0,
        excluded_count=0,
    )


def test_real_postgres_historical_eligibility_and_concurrent_versions(pg_engine) -> None:
    """Use the Alembic-provisioned database so migration-only constraints are exercised."""

    eligible = models.Hospital(name="8월 서비스 의원", slug=f"month-eligible-{uuid.uuid4().hex}")
    ended = models.Hospital(name="7월 종료 의원", slug=f"month-ended-{uuid.uuid4().hex}")
    with Session(pg_engine) as setup:
        setup.add_all((eligible, ended))
        setup.flush()
        setup.add_all(
            (
                models.HospitalServiceInterval(
                    hospital_id=eligible.id,
                    started_at=datetime(2026, 8, 10, tzinfo=KST),
                    ended_at=datetime(2026, 8, 20, tzinfo=KST),
                    provenance="ACTIVATION",
                ),
                models.HospitalServiceInterval(
                    hospital_id=ended.id,
                    started_at=datetime(2026, 7, 1, tzinfo=KST),
                    ended_at=datetime(2026, 8, 1, tzinfo=KST),
                    provenance="ACTIVATION",
                ),
            )
        )
        setup.commit()
        eligible_id, ended_id = eligible.id, ended.id

    first_locked = threading.Event()
    release_first = threading.Event()

    def create_once(first: bool) -> tuple[bool, int]:
        with Session(pg_engine) as db:
            plan = lock_report_version_plan(
                db,
                hospital_id=eligible_id,
                period=reporting_period(2026, 8),
                reason_code=ReportBuildReason.SCHEDULED_CLOSE,
                correlation_key="scheduled:eligible:2026-08",
            )
            if first:
                first_locked.set()
                assert release_first.wait(timeout=5)
            if plan.create:
                db.add(_report(eligible_id, plan.version, plan.supersedes_report_id))
            db.commit()
            return plan.create, plan.version

    try:
        with Session(pg_engine) as db:
            selected = set(eligible_hospital_ids(db, reporting_period(2026, 8)))
        assert eligible_id in selected
        assert ended_id not in selected

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(create_once, True)
            assert first_locked.wait(timeout=5)
            second = pool.submit(create_once, False)
            release_first.set()
            outcomes = [first.result(timeout=10), second.result(timeout=10)]
        assert sorted(outcomes) == [(False, 1), (True, 1)]

        with Session(pg_engine) as db:
            v1 = db.scalars(
                select(models.MonthlyReport).where(models.MonthlyReport.hospital_id == eligible_id)
            ).one()
            late = lock_report_version_plan(
                db,
                hospital_id=eligible_id,
                period=reporting_period(2026, 8),
                reason_code=ReportBuildReason.LATE_DATA_REBUILD,
                correlation_key="operation-run:late-august",
            )
            assert late.version == 2
            assert late.supersedes_report_id == v1.id
            db.add(_report(eligible_id, late.version, late.supersedes_report_id))
            db.commit()
            versions = db.scalars(
                select(models.MonthlyReport.version)
                .where(models.MonthlyReport.hospital_id == eligible_id)
                .order_by(models.MonthlyReport.version)
            ).all()
            assert list(versions) == [1, 2]
    finally:
        # Keep the shared integration database clean; delete the self-referencing
        # report chain from newest to oldest before removing the hospitals.
        with Session(pg_engine) as cleanup:
            reports = cleanup.scalars(
                select(models.MonthlyReport)
                .where(models.MonthlyReport.hospital_id == eligible_id)
                .order_by(models.MonthlyReport.version.desc())
            ).all()
            for report in reports:
                cleanup.delete(report)
                cleanup.flush()
            cleanup.execute(
                delete(models.HospitalServiceInterval).where(
                    models.HospitalServiceInterval.hospital_id.in_((eligible_id, ended_id))
                )
            )
            cleanup.execute(
                delete(models.Hospital).where(models.Hospital.id.in_((eligible_id, ended_id)))
            )
            cleanup.commit()
