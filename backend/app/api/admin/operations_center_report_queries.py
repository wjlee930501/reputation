"""Set-based monthly-report queue query for the operations center."""

import uuid
from datetime import datetime, timedelta
from typing import Final, Literal, assert_never
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Select, and_, case, func, or_, select
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
from app.models.monthly_control import HospitalServiceInterval, ReportDeliveryEventType
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
from app.services.monthly_period import reporting_period

ReportQueueState = Literal["MISSING", "DELIVERY_PENDING"]
_REPORT_ACTION_LABEL: Final = "보고서 확인"
_ACTIVE_MONTHLY_RUN_STATES: Final = (
    OperationRunState.REQUESTED.value,
    OperationRunState.QUEUED.value,
    OperationRunState.RUNNING.value,
)
_MONTHLY_RUN_TYPES: Final = ("GENERATE_MONTHLY_REPORT", "SCHEDULED_MONTHLY_REPORT")


def _report_operator_copy(state: ReportQueueState) -> tuple[str, str]:
    match state:
        case "MISSING":
            return (
                "지난달 보고서가 없어 원장 보고가 지연됩니다.",
                "운영 센터에서 처리 사유를 남기고 “지난달 리포트 생성”을 누르세요. "
                "생성 작업이 시작되면 작업 기록에서 진행 상태를 확인하세요. 버튼이 없거나 "
                "작업이 시작되지 않으면 개발팀에 병원명과 현재 화면의 문구를 전달하세요.",
            )
        case "DELIVERY_PENDING":
            return (
                "생성된 지난달 보고서의 원장 전달 검수가 완료되지 않았습니다.",
                f"운영 센터의 “{_REPORT_ACTION_LABEL}”을 눌러 고객용 PDF와 전달 기록을 확인하세요.",
            )
        case unreachable:
            assert_never(unreachable)


def _report_action(
    hospital_id: uuid.UUID,
    *,
    state: ReportQueueState,
    year: int,
    month: int,
) -> OperationsAction:
    """Expose the least-click recovery available for each report queue state."""
    if state == "MISSING":
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


def _previous_period(now: datetime) -> tuple[int, int, datetime, datetime]:
    local = now.astimezone(ZoneInfo("Asia/Seoul"))
    year, month = (local.year, local.month - 1) if local.month > 1 else (local.year - 1, 12)
    period = reporting_period(year, month)
    return year, month, period.starts_at, period.ends_at


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
    if filters.severity not in (None, "HIGH") or filters.sla not in (
        None,
        SlaFilter.OVERDUE,
    ):
        return 0, []
    year, month, period_start, period_end = _previous_period(now)
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
    report_state = case((MonthlyReport.id.is_(None), "MISSING"), else_="DELIVERY_PENDING")
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
    owner_filter = owner_predicate(assignee, filters.owner)
    if owner_filter is not None:
        predicates.append(owner_filter)
    if filters.status:
        predicates.append(report_state == filters.status)

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

    count_stmt = with_joins(select(func.count(Hospital.id)).select_from(Hospital)).where(
        *predicates
    )
    page_stmt = with_joins(
        select(
            Hospital,
            MonthlyReport,
            HospitalHandoff,
            assignee,
            report_state.label("report_state"),
            func.count().over().label("_total"),
        )
    ).where(*predicates)
    page_stmt = (
        page_stmt.order_by(Hospital.name, Hospital.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(page_stmt)).all())
    total = int(rows[0]._total) if overview and rows else 0
    if not overview:
        total = int((await db.scalar(count_stmt)) or 0)

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
            severity="HIGH",
            impact=_report_operator_copy(report_state_value)[0],
            owner=owner_projection(actor),
            sla_due_at=handoff.sla_due_at if handoff else None,
            sla_state="OVERDUE",
            next_action=_report_operator_copy(report_state_value)[1],
            action=_report_action(
                hospital.id,
                state=report_state_value,
                year=year,
                month=month,
            ),
            retry=None,
            safe_cause=None,
            history=[OperationsHistoryEntry(event="REPORT_READY", at=report.created_at)]
            if report
            else [],
            slack=None,
            report_id=report.id if report else None,
            occurred_at=report.created_at if report else period_start,
        )
        for hospital, report, handoff, actor, report_state_value, _total in rows
    ]


__all__ = ("load_reports_queue",)
