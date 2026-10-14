"""Repair the durable report-to-incident projection after a worker crash."""

from __future__ import annotations

import uuid
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
    validate_persisted_report_artifact,
)
from app.workers.monthly_artifact_incident_contracts import MonthlyArtifactIncidentContext
from app.workers.monthly_artifact_incident_control import ensure_monthly_artifact_failure_batch
from app.workers.monthly_artifact_recovery_control import recover_monthly_artifact_failure_batch
from app.workers.monthly_report_safety_net import (
    dispatch_report_repair,
    run_monthly_report_safety_net,
)

_SOURCE_TYPE = "MONTHLY_REPORT_ARTIFACT"
_BATCH_SIZE = 100
_CANDIDATE_LOOKBACK = timedelta(days=45)
_SAFETY_NET_EVERY_MINUTES = 5


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
def reconcile_monthly_artifact_incidents() -> dict[str, int | str | dict[str, int | str]]:
    """Converge latest report truth with its active incident and notification.

    원장 PDF가 유효하지 않은 최종 판은 사람을 기다리지 않고 다시 만든다(안전망과 같은 병원·월 예산).
    5분마다 월간 보고서 안전망(`monthly_report_safety_net`)도 함께 돈다 — RedBeat 판을 늘리지 않는다.
    """

    opened = 0
    recovered = 0
    observed_at = datetime.now(timezone.utc)
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
        candidate_cutoff = observed_at - _CANDIDATE_LOOKBACK
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
        repair_targets: list[tuple[Hospital, MonthlyReport]] = []
        handled_periods: set[tuple[uuid.UUID, int, int]] = set()
        for report, hospital, artifact, incident in rows:
            handled_periods.add((hospital.id, report.period_year, report.period_month))
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
                # 측정 커버리지와 무관하게 PDF가 유효하면 PDF 사고는 닫는다. 커버리지 미완료 판을
                # 건너뛰던 탓에 유효한 PDF의 사고가 6~8일 열려 있었다(2026-09 장앤김·신기한).
                if incident is not None:
                    recovery_contexts.append(context)
                continue
            if not coverage_is_final(report):
                continue
            repair_targets.append((hospital, report))
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
        recovery_contexts.extend(_settled_orphan_contexts(db, handled_periods))
        if recovery_contexts:
            recovered = anyio.run(
                recover_monthly_artifact_failure_batch, recovery_contexts
            )
        opened = sum(int(created) for _incident_id, created in failure_results)
        repairs = 0
        for hospital, report in repair_targets:
            decision = dispatch_report_repair(
                db,
                hospital,
                report.period_year,
                report.period_month,
                prefer_template=True,
                now=observed_at,
            )
            repairs += int(decision.action == "dispatched")
        safety_net: dict[str, int | str] = {"status": "skipped"}
        if _safety_net_due(observed_at):
            safety_net = run_monthly_report_safety_net(db, now=observed_at)
    return {
        "status": "completed",
        "opened_count": opened,
        "recovered_count": recovered,
        "repair_dispatched_count": repairs,
        "safety_net": safety_net,
    }


def _safety_net_due(observed_at: datetime) -> bool:
    return observed_at.minute % _SAFETY_NET_EVERY_MINUTES == 0


def _settled_orphan_contexts(
    db, handled_periods: set[tuple[uuid.UUID, int, int]]
) -> list[MonthlyArtifactIncidentContext]:
    """이번 실행의 월간 판 대조에 들지 않은 열린 PDF 사고를 그 달 보고서 사실로 정리한다.

    2026-10-09 이전 대조 작업은 V0 보고서에도 월간 원장 계약을 적용해 파일과 hash가 같은 V0 PDF를
    'hash 불일치'로 열었다(V0와 월간이 같은 `병원:연-월` 사고 키를 쓴다). 그 뒤 대조가 월간만 보게
    되면서 월간 보고서가 없는 달(장보고·뉴플랜 10월)의 사고는 아무도 다시 판정하지 않았다. 그 달의
    최신 월간 판(없으면 최신 V0)이 자기 계약으로 유효하면 닫는다.
    """
    incidents = db.execute(
        select(Incident, Hospital)
        .join(Hospital, Hospital.id == Incident.hospital_id)
        .where(
            Incident.source_type == _SOURCE_TYPE,
            Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
        )
        .limit(_BATCH_SIZE)
    ).all()
    contexts: list[MonthlyArtifactIncidentContext] = []
    for incident, hospital in incidents:
        period = _incident_period(incident)
        if period is None or (hospital.id, *period) in handled_periods:
            continue
        year, month = period
        reports = db.scalars(
            select(MonthlyReport)
            .where(
                MonthlyReport.hospital_id == hospital.id,
                MonthlyReport.period_year == year,
                MonthlyReport.period_month == month,
                MonthlyReport.report_type.in_(("MONTHLY", "V0")),
            )
            .order_by(MonthlyReport.version.desc(), MonthlyReport.created_at.desc())
        ).all()
        monthly = next((row for row in reports if row.report_type == "MONTHLY"), None)
        report = monthly or next((row for row in reports if row.report_type == "V0"), None)
        if report is None:
            continue
        artifact = db.scalars(
            select(MonthlyReportArtifact).where(
                MonthlyReportArtifact.report_id == report.id,
                MonthlyReportArtifact.audience == "DOCTOR",
            )
        ).first()
        if not validate_persisted_report_artifact(report, artifact).valid:
            continue
        contexts.append(
            MonthlyArtifactIncidentContext(
                hospital_id=hospital.id,
                hospital_name=hospital.name,
                report_id=report.id,
                year=year,
                month=month,
                operation_run_id=None,
            )
        )
    return contexts


def _incident_period(incident: Incident) -> tuple[int, int] | None:
    _hospital, separator, tail = str(incident.source_id or "").rpartition(":")
    try:
        year_text, month_text = tail.split("-", 1)
        year, month = int(year_text), int(month_text)
    except ValueError:
        return None
    return (year, month) if separator and 1 <= month <= 12 else None


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
