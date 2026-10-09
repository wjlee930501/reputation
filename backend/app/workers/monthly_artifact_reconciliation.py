"""Repair the durable report-to-incident projection after a worker crash."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import anyio
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import String, and_, cast, exists, func, or_, select
from sqlalchemy.orm import aliased

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyReportArtifact
from app.models.operations import Incident, IncidentState, OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.services.monthly_report_delivery import coverage_is_final
from app.services.report_artifact_validation import (
    DoctorPdfValidationError,
    validate_persisted_doctor_artifact,
)
from app.workers.monthly_artifact_incident_contracts import MonthlyArtifactIncidentContext
from app.workers.monthly_artifact_incident_control import ensure_monthly_artifact_failure_batch
from app.workers.monthly_artifact_recovery_control import recover_monthly_artifact_failure_batch

_SOURCE_TYPE = "MONTHLY_REPORT_ARTIFACT"
_BATCH_SIZE = 100
_CANDIDATE_LOOKBACK = timedelta(days=45)


class _RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: str
    period_year: int
    period_month: int
    milestones: list[str] | None = None
    report_id: str | None = None
    report_version: int | None = None
    measurement_quality: str | None = None
    observation_adequacy: dict[str, object] | None = None
    supersedes_report_id: str | None = None


@celery_app.task(name="app.workers.monthly_artifact_reconciliation.reconcile")
def reconcile_monthly_artifact_incidents() -> dict[str, int | str]:
    """Converge latest report truth with its active incident and notification."""

    opened = 0
    recovered = 0
    with SyncSessionLocal() as db:
        period_key = func.concat(
            cast(MonthlyReport.hospital_id, String),
            ":",
            cast(MonthlyReport.period_year, String),
            "-",
            func.lpad(cast(MonthlyReport.period_month, String), 2, "0"),
        )
        newer = aliased(MonthlyReport)
        is_latest = ~exists(
            select(newer.id).where(
                newer.hospital_id == MonthlyReport.hospital_id,
                newer.period_year == MonthlyReport.period_year,
                newer.period_month == MonthlyReport.period_month,
                newer.report_type == MonthlyReport.report_type,
                newer.version > MonthlyReport.version,
            )
        )
        candidate_cutoff = datetime.now(timezone.utc) - _CANDIDATE_LOOKBACK
        rows = db.execute(
            select(MonthlyReport, Hospital, MonthlyReportArtifact, Incident)
            .join(Hospital, Hospital.id == MonthlyReport.hospital_id)
            .outerjoin(
                MonthlyReportArtifact,
                and_(
                    MonthlyReportArtifact.report_id == MonthlyReport.id,
                    MonthlyReportArtifact.audience == "DOCTOR",
                ),
            )
            .outerjoin(
                Incident,
                and_(
                    Incident.hospital_id == MonthlyReport.hospital_id,
                    Incident.source_type == _SOURCE_TYPE,
                    Incident.source_id == period_key,
                    Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
                ),
            )
            .where(
                MonthlyReport.report_type == "MONTHLY",
                is_latest,
                or_(
                    MonthlyReport.created_at >= candidate_cutoff,
                    Incident.id.is_not(None),
                ),
            )
            .order_by(MonthlyReport.created_at, MonthlyReport.id)
            .limit(_BATCH_SIZE)
        ).all()
        hospital_ids = {hospital.id for _report, hospital, _artifact, _incident in rows}
        runs = (
            list(
                db.execute(
                    select(OperationRun)
                    .where(
                        OperationRun.hospital_id.in_(hospital_ids),
                        OperationRun.operation_type.in_(
                            ("GENERATE_MONTHLY_REPORT", "SCHEDULED_MONTHLY_REPORT")
                        ),
                        OperationRun.state.in_(
                            (
                                OperationRunState.RUNNING,
                                OperationRunState.PARTIAL,
                                OperationRunState.SUCCEEDED,
                            )
                        ),
                    )
                    .order_by(OperationRun.created_at.desc())
                ).scalars()
            )
            if hospital_ids
            else []
        )
        failure_items: list[
            tuple[MonthlyArtifactIncidentContext, DoctorPdfValidationError]
        ] = []
        recovery_contexts: list[MonthlyArtifactIncidentContext] = []
        for report, hospital, artifact, incident in rows:
            if not coverage_is_final(report):
                continue
            run = next(
                (
                    candidate
                    for candidate in runs
                    if candidate.hospital_id == hospital.id
                    and _run_matches_report(candidate, report)
                ),
                None,
            )
            context = MonthlyArtifactIncidentContext(
                hospital_id=hospital.id,
                hospital_name=hospital.name,
                report_id=report.id,
                year=report.period_year,
                month=report.period_month,
                operation_run_id=run.id if run is not None else None,
            )
            artifact_valid = _artifact_is_valid(report, artifact)
            if artifact_valid:
                if incident is not None:
                    recovery_contexts.append(context)
                continue
            if incident is None:
                if report.doctor_pdf_path is None:
                    error = DoctorPdfValidationError(
                        "DOCTOR_PDF_INCIDENT_RECONCILED",
                        "원장 전달용 PDF 생성은 중단됐지만 운영 알림이 남지 않아 자동으로 복구했습니다.",
                    )
                else:
                    error = DoctorPdfValidationError(
                        "DOCTOR_PDF_ARTIFACT_INVALID",
                        "저장된 원장 전달용 PDF의 검증 정보가 파일과 일치하지 않습니다.",
                    )
                failure_items.append((context, error))
        failure_results = (
            anyio.run(
                ensure_monthly_artifact_failure_batch,
                failure_items,
            )
            if failure_items
            else []
        )
        if recovery_contexts:
            recovered = anyio.run(
                recover_monthly_artifact_failure_batch, recovery_contexts
            )
        opened = sum(int(created) for _incident_id, created in failure_results)
    return {
        "status": "completed",
        "opened_count": opened,
        "recovered_count": recovered,
    }


def _run_matches_report(run: OperationRun, report: MonthlyReport) -> bool:
    try:
        summary = _RunSummary.model_validate(run.result_summary)
    except ValidationError:
        return False
    return bool(
        summary.period_year == report.period_year
        and summary.period_month == report.period_month
        and (summary.report_id is None or summary.report_id == str(report.id))
    )


def _artifact_is_valid(
    report: MonthlyReport,
    artifact: MonthlyReportArtifact | None,
) -> bool:
    return validate_persisted_doctor_artifact(report, artifact).valid
