"""Set-based monthly-report queue query for the operations center."""

import uuid
from datetime import datetime, timedelta
from typing import Final, Literal, assert_never, cast
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.admin.operations_center_query_common import (
    OperationsFilters,
    SlaFilter,
    owner_predicate,
)
from app.api.admin.operations_center_serializers import owner_projection
from app.models.admin_user import AdminUser
from app.models.handoff import HospitalHandoff
from app.models.hospital import Hospital
from app.models.monthly_control import (
    HospitalServiceInterval,
    MonthlyMeasurementManifest,
    MonthlyReportArtifact,
    ReportDeliveryEventType,
)
from app.models.operations import OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.schemas.operations import (
    OperationsAction,
    OperationsCustomer,
    OperationsHistoryEntry,
    OperationsQueue,
    OperationsQueueRow,
)
from app.services.monthly_delivery_projection import (
    latest_delivery_event_subquery,
    latest_monthly_report_subquery,
)
from app.services.monthly_period import monthly_final_judgment_at, reporting_period
from app.services.monthly_report_delivery import monthly_report_delivery_gate

ReportQueueState = Literal[
    "MISSING",
    "COVERAGE_INCOMPLETE",
    "MANIFEST_MISMATCH",
    "MANIFEST_OPEN",
    "DOCTOR_ARTIFACT_MISSING",
    "DOCTOR_ARTIFACT_INVALID",
    "REPORT_BLOCKED",
    "DELIVERY_PENDING",
]
_REPORT_ACTION_LABEL: Final = "보고서 확인"
_ACTIVE_MONTHLY_RUN_STATES: Final = (
    OperationRunState.REQUESTED.value,
    OperationRunState.QUEUED.value,
    OperationRunState.RUNNING.value,
)
_MONTHLY_RUN_TYPES: Final = ("GENERATE_MONTHLY_REPORT", "SCHEDULED_MONTHLY_REPORT")


def _report_operator_copy(
    state: ReportQueueState, *, preparing: bool = False
) -> tuple[str, str]:
    match state:
        case "MISSING":
            if preparing:
                return (
                    "지난달 보고서를 시스템이 자동으로 준비하고 있습니다.",
                    "1일 새벽 측정 뒤 09:00까지 시스템이 보고서를 만듭니다. 수동으로 생성하거나 "
                    "재측정하지 마세요. 09:00 뒤에도 없으면 처리 사유와 함께 다시 표시됩니다.",
                )
            return (
                "지난달 보고서가 없어 원장 보고가 지연됩니다.",
                "시스템이 매월 1일 새벽에 측정하고 오전에 지난달 보고서를 만듭니다. "
                "남아 있으면 처리 사유를 남기고 “지난달 리포트 생성”을 누른 뒤 "
                "작업 기록에서 진행 상태를 확인하세요.",
            )
        case "DELIVERY_PENDING":
            return (
                "생성된 지난달 보고서의 원장 전달 검수가 완료되지 않았습니다.",
                f"운영 센터의 “{_REPORT_ACTION_LABEL}”을 눌러 고객용 PDF와 전달 기록을 확인하세요.",
            )
        case "COVERAGE_INCOMPLETE":
            if preparing:
                return (
                    "지난달 측정과 보고서를 시스템이 자동으로 준비하고 있습니다.",
                    "1일 새벽 창에서 측정을 마치고 09:00까지 보고서를 준비합니다. 수동으로 "
                    "재측정하거나 보고서를 만들지 마세요. 09:00 뒤에도 미완료면 사유별 조치가 표시됩니다.",
                )
            return (
                "필수 측정이 완료되지 않아 지난달 보고서를 원장님께 전달할 수 없습니다.",
                "보고서 확인에서 남은 계획·성공·실패 수와 측정 상태를 확인하고, "
                "차단 사유를 해결한 뒤 다시 확인하세요.",
            )
        case "MANIFEST_MISMATCH" | "MANIFEST_OPEN":
            return (
                "지난달 측정 집계가 보고 기간과 맞지 않거나 아직 마감되지 않았습니다.",
                "보고서 확인에서 측정 집계 연결과 마감 상태를 확인하세요.",
            )
        case "DOCTOR_ARTIFACT_MISSING":
            return (
                "원장 전달용 PDF가 없어 지난달 보고서를 전달할 수 없습니다.",
                "보고서 확인에서 원장 전달용 PDF 생성 상태를 확인하세요.",
            )
        case "DOCTOR_ARTIFACT_INVALID":
            return (
                "원장 전달용 PDF 검증이 실패해 지난달 보고서를 전달할 수 없습니다.",
                "보고서 확인에서 PDF 검증 실패 사유를 확인하세요.",
            )
        case "REPORT_BLOCKED":
            return (
                "지난달 보고서의 필수 운영 검수가 끝나지 않아 전달할 수 없습니다.",
                "보고서 확인에서 표시된 차단 사유를 해결한 뒤 전달 가능 상태를 다시 확인하세요.",
            )
        case unreachable:
            assert_never(unreachable)


def _report_action(
    hospital_id: uuid.UUID,
    *,
    state: ReportQueueState,
    year: int,
    month: int,
    preparing: bool = False,
) -> OperationsAction:
    """Expose the least-click recovery available for each report queue state.

    Before the 09:00 availability judgment the queue is view-only — the automatic
    dawn window owns measurement and report preparation.
    """
    if state == "MISSING" and not preparing:
        return OperationsAction(
            kind="GENERATE_MONTHLY_REPORT",
            label="지난달 리포트 생성",
            method="POST",
            path=(
                f"/hospitals/{hospital_id}/operations/generate-monthly-report"
                f"?year={year}&month={month}"
            ),
            reason_required=True,
            requires_idempotency_key=True,
        )
    return OperationsAction(
        kind="OPEN_REPORT",
        label=_REPORT_ACTION_LABEL,
        method="GET",
        path=f"/hospitals/{hospital_id}/reports",
    )


def _report_queue_state(
    report: MonthlyReport | None,
    manifest: MonthlyMeasurementManifest | None,
    artifact: MonthlyReportArtifact | None,
) -> tuple[ReportQueueState, str | None]:
    """Project the reports queue from the same gate that protects mark-sent."""
    if report is None:
        return "MISSING", None
    gate = monthly_report_delivery_gate(report, manifest, artifact)
    if gate.ready:
        return "DELIVERY_PENDING", None
    if gate.code is None:
        return "REPORT_BLOCKED", gate.message
    state = gate.code.upper()
    if state not in {
        "COVERAGE_INCOMPLETE",
        "MANIFEST_MISMATCH",
        "MANIFEST_OPEN",
        "DOCTOR_ARTIFACT_MISSING",
        "DOCTOR_ARTIFACT_INVALID",
        "REPORT_BLOCKED",
    }:
        state = "REPORT_BLOCKED"
    return cast(ReportQueueState, state), gate.message


def _previous_period(now: datetime) -> tuple[int, int, datetime, datetime, datetime]:
    local = now.astimezone(ZoneInfo("Asia/Seoul"))
    year, month = (local.year, local.month - 1) if local.month > 1 else (local.year - 1, 12)
    period = reporting_period(year, month)
    return year, month, period.starts_at, period.ends_at, period.closes_at


def _eligible_hospital_ids_stmt(
    period_start: datetime, period_end: datetime
) -> Select[tuple[uuid.UUID]]:
    """Use the same historical service boundary as the scheduled monthly worker."""

    return (
        select(HospitalServiceInterval.hospital_id)
        .where(
            HospitalServiceInterval.started_at < period_end,
            or_(
                HospitalServiceInterval.ended_at.is_(None),
                HospitalServiceInterval.ended_at > period_start,
            ),
        )
        .distinct()
    )


def _active_monthly_run_period_predicate(year: int, month: int) -> ColumnElement[bool]:
    """Match active report work already covering this operations queue period."""
    period_key = f"{year}-{month:02d}"
    return or_(
        and_(
            OperationRun.result_summary["period_year"].as_integer() == year,
            OperationRun.result_summary["period_month"].as_integer() == month,
        ),
        and_(
            OperationRun.request_payload["_dispatch"]["task_args"][1].as_integer() == year,
            OperationRun.request_payload["_dispatch"]["task_args"][2].as_integer() == month,
        ),
        OperationRun.request_payload["source_id"].as_string() == period_key,
    )


def _fresh_active_monthly_run_predicate(now: datetime) -> ColumnElement[bool]:
    """Hide only live monthly work; stale RUNNING rows must become operator-visible."""

    return (
        func.coalesce(
            OperationRun.heartbeat_at,
            OperationRun.started_at,
            OperationRun.queued_at,
            OperationRun.requested_at,
        )
        > now - timedelta(hours=1)
    )


async def load_reports_queue(
    db: AsyncSession,
    filters: OperationsFilters,
    *,
    page: int,
    page_size: int,
    overview: bool,
    now: datetime,
) -> tuple[int, list[OperationsQueueRow]]:
    """Load hospitals missing or awaiting delivery of the prior monthly report."""
    if filters.severity not in (None, "HIGH", "LOW") or filters.sla not in (
        None,
        SlaFilter.NONE,
    ):
        return 0, []
    year, month, period_start, period_end, closes_at = _previous_period(now)
    days_since_close = max(
        0,
        (
            now.astimezone(ZoneInfo("Asia/Seoul")).date()
            - closes_at.astimezone(ZoneInfo("Asia/Seoul")).date()
        ).days,
    )
    # 1일 09:00 판정 전에는 미생성·측정 미완료가 운영자 작업이 아니라 준비 상태다.
    preparing = now.astimezone(ZoneInfo("Asia/Seoul")) < monthly_final_judgment_at(
        year, month
    )
    latest = latest_monthly_report_subquery(year, month)
    latest_delivery = latest_delivery_event_subquery()
    active_report_runs = (
        select(OperationRun.hospital_id)
        .where(
            OperationRun.operation_type.in_(_MONTHLY_RUN_TYPES),
            OperationRun.state.in_(_ACTIVE_MONTHLY_RUN_STATES),
            _active_monthly_run_period_predicate(year, month),
            _fresh_active_monthly_run_predicate(now),
        )
        .group_by(OperationRun.hospital_id)
        .subquery()
    )
    assignee = aliased(AdminUser)
    predicates = [
        Hospital.id.in_(_eligible_hospital_ids_stmt(period_start, period_end)),
        active_report_runs.c.hospital_id.is_(None),
        or_(
            MonthlyReport.id.is_(None),
            and_(
                latest_delivery.c.report_id.is_(None),
                MonthlyReport.sent_at.is_(None),
            ),
            latest_delivery.c.event_type == ReportDeliveryEventType.RESCINDED.value,
        ),
    ]
    if filters.hospital_id is not None:
        predicates.append(Hospital.id == filters.hospital_id)
    owner_filter = owner_predicate(assignee, filters.owner)
    if owner_filter is not None:
        predicates.append(owner_filter)

    def with_joins(statement):
        return (
            statement.outerjoin(
                latest,
                and_(latest.c.hospital_id == Hospital.id, latest.c.rn == 1),
            )
            .outerjoin(active_report_runs, active_report_runs.c.hospital_id == Hospital.id)
            .outerjoin(
                MonthlyReport,
                and_(
                    MonthlyReport.id == latest.c.report_id,
                    MonthlyReport.period_year == year,
                    MonthlyReport.period_month == month,
                    MonthlyReport.report_type == "MONTHLY",
                ),
            )
            .outerjoin(
                latest_delivery,
                and_(
                    latest_delivery.c.report_id == MonthlyReport.id,
                    latest_delivery.c.rn == 1,
                ),
            )
            .outerjoin(HospitalHandoff, HospitalHandoff.hospital_id == Hospital.id)
            .outerjoin(assignee, assignee.id == HospitalHandoff.ae_owner_id)
        )

    candidate_stmt = with_joins(
        select(
            Hospital,
            MonthlyReport,
            MonthlyMeasurementManifest,
            MonthlyReportArtifact,
            HospitalHandoff,
            assignee,
        )
    )
    candidate_stmt = (
        candidate_stmt.outerjoin(
            MonthlyMeasurementManifest,
            MonthlyMeasurementManifest.id == MonthlyReport.manifest_id,
        )
        .outerjoin(
            MonthlyReportArtifact,
            and_(
                MonthlyReportArtifact.report_id == MonthlyReport.id,
                MonthlyReportArtifact.audience == "DOCTOR",
            ),
        )
        .where(*predicates)
        .order_by(Hospital.name, Hospital.id)
    )
    candidates = []
    for hospital, report, manifest, artifact, handoff, actor in (
        await db.execute(candidate_stmt)
    ).all():
        report_state_value, safe_cause = _report_queue_state(report, manifest, artifact)
        if filters.status is not None and report_state_value != filters.status:
            continue
        severity = (
            "LOW"
            if preparing and report_state_value in {"MISSING", "COVERAGE_INCOMPLETE"}
            else "HIGH"
        )
        if filters.severity is not None and severity != filters.severity:
            continue
        candidates.append(
            (hospital, report, handoff, actor, report_state_value, safe_cause, severity)
        )
    total = len(candidates)
    rows = candidates[(page - 1) * page_size : page * page_size]

    return total, [
        OperationsQueueRow(
            id=f"report:{report.id if report else hospital.id}:{year}-{month:02d}",
            queue=OperationsQueue.REPORTS,
            customer=OperationsCustomer(
                hospital_id=hospital.id,
                name=hospital.name,
                admin_path=f"/hospitals/{hospital.id}/reports",
            ),
            status=report_state_value,
            severity=severity,
            impact=_report_operator_copy(
                report_state_value,
                preparing=preparing,
            )[0],
            owner=owner_projection(actor),
            # REPORTS는 전달 준비 상태를 보여 주는 큐이지 스태프 SLA 큐가 아니다.
            # 월간 집계 마감은 커버리지/전달 게이트이며 운영자 처리 기한으로 투영하지 않는다.
            sla_due_at=None,
            sla_state="NONE",
            days_since_close=days_since_close,
            next_action=_report_operator_copy(
                report_state_value,
                preparing=preparing,
            )[1],
            action=_report_action(
                hospital.id,
                state=report_state_value,
                year=year,
                month=month,
                preparing=preparing,
            ),
            retry=None,
            safe_cause=safe_cause,
            history=[OperationsHistoryEntry(event="REPORT_READY", at=report.created_at)]
            if report
            else [],
            slack=None,
            report_id=report.id if report else None,
            occurred_at=report.created_at if report else period_start,
        )
        for hospital, report, handoff, actor, report_state_value, safe_cause, severity in rows
    ]


__all__ = ("load_reports_queue",)
