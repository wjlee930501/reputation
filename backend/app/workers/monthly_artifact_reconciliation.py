"""Repair the durable report-to-incident projection after a worker crash."""

from __future__ import annotations

import anyio
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import String, and_, cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import aliased

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyReportArtifact
from app.models.operations import Incident, IncidentState, OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.services.report_artifact_validation import (
    DoctorPdfValidationError,
    parse_doctor_artifact_metadata,
)
from app.workers.monthly_artifact_incident_contracts import MonthlyArtifactIncidentContext
from app.workers.monthly_artifact_incident_control import ensure_monthly_artifact_failure_batch
from app.workers.monthly_artifact_recovery_control import recover_monthly_artifact_failure_batch

_BLOCKER = "DOCTOR_ARTIFACT_UNVALIDATED"
_SOURCE_TYPE = "MONTHLY_REPORT_ARTIFACT"
_BATCH_SIZE = 100


class _RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: str
    period_year: int
    period_month: int
    milestones: list[str] | None = None
    report_id: str | None = None
    report_version: int | None = None
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
        metadata = cast(MonthlyReportArtifact.validation_metadata, JSONB)
        metadata_keys = (
            "validation_version",
            "validation_source",
            "page_count",
            "page_size",
            "glyph_count",
            "font_family",
            "font_embedded",
            "korean_to_unicode",
            "link_count",
            "expected_link_present",
            "required_text_present",
            "sha256",
            "byte_size",
        )
        canonical_metadata = func.jsonb_build_object(
            *(item for key in metadata_keys for item in (key, metadata.op("->")(key)))
        )
        glyph_text = metadata.op("->>")("glyph_count")
        link_text = metadata.op("->>")("link_count")
        sql_artifact_valid = and_(
            MonthlyReportArtifact.id.is_not(None),
            MonthlyReportArtifact.validated.is_(True),
            MonthlyReportArtifact.path == MonthlyReport.doctor_pdf_path,
            MonthlyReportArtifact.sha256 == metadata.op("->>")("sha256"),
            cast(MonthlyReportArtifact.byte_size, String)
            == metadata.op("->>")("byte_size"),
            metadata == canonical_metadata,
            metadata.op("->>")("validation_version") == "doctor-pdf-v1",
            metadata.op("->>")("validation_source") == "SYSTEM",
            metadata.op("->>")("page_count") == "1",
            metadata.op("->>")("page_size") == "A4",
            glyph_text.op("~")(r"^[1-9][0-9]*$"),
            metadata.op("->>")("font_family") == "Pretendard",
            metadata.op("->>")("font_embedded") == "true",
            metadata.op("->>")("korean_to_unicode") == "true",
            link_text.op("~")(r"^[1-9][0-9]*$"),
            metadata.op("->>")("expected_link_present") == "true",
            metadata.op("->>")("required_text_present") == "true",
        )
        invalid_truth = or_(
            and_(
                MonthlyReport.doctor_pdf_path.is_(None),
                cast(MonthlyReport.delivery_blockers, String).contains(_BLOCKER),
            ),
            and_(MonthlyReport.doctor_pdf_path.is_not(None), ~sql_artifact_valid),
        )
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
                MonthlyReport.quality == "COMPLETE",
                is_latest,
                or_(
                    and_(invalid_truth, Incident.id.is_(None)),
                    and_(sql_artifact_valid, Incident.id.is_not(None)),
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
            if _artifact_is_valid(report, artifact) and incident is not None:
                recovery_contexts.append(context)
            elif incident is None:
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
    if artifact is None:
        return False
    metadata = parse_doctor_artifact_metadata(artifact.validation_metadata)
    return bool(
        artifact.validated
        and artifact.path == report.doctor_pdf_path
        and metadata is not None
        and metadata.sha256 == artifact.sha256
        and metadata.byte_size == artifact.byte_size
    )
