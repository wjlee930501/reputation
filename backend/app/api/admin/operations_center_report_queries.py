"""Set-based monthly-report queue query for the operations center."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
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
from app.models.hospital import Hospital, HospitalStatus
from app.models.report import MonthlyReport
from app.schemas.operations import (
    OperationsAction,
    OperationsCustomer,
    OperationsHistoryEntry,
    OperationsQueue,
    OperationsQueueRow,
)


def _previous_period(now: datetime) -> tuple[int, int, datetime]:
    local = now.astimezone(ZoneInfo("Asia/Seoul"))
    year, month = (local.year, local.month - 1) if local.month > 1 else (local.year - 1, 12)
    return year, month, datetime(year, month, 1, tzinfo=ZoneInfo("Asia/Seoul"))


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
    year, month, period_start = _previous_period(now)
    latest = (
        select(MonthlyReport.hospital_id, func.max(MonthlyReport.version).label("version"))
        .where(
            MonthlyReport.period_year == year,
            MonthlyReport.period_month == month,
            MonthlyReport.report_type == "MONTHLY",
        )
        .group_by(MonthlyReport.hospital_id)
        .subquery()
    )
    assignee = aliased(AdminUser)
    report_state = case((MonthlyReport.id.is_(None), "MISSING"), else_="DELIVERY_PENDING")
    predicates = [
        Hospital.status == HospitalStatus.ACTIVE,
        Hospital.created_at < period_start,
        or_(MonthlyReport.id.is_(None), MonthlyReport.sent_at.is_(None)),
    ]
    owner_filter = owner_predicate(assignee, filters.owner)
    if owner_filter is not None:
        predicates.append(owner_filter)
    if filters.status:
        predicates.append(report_state == filters.status)

    def with_joins(statement):
        return (
            statement.outerjoin(latest, latest.c.hospital_id == Hospital.id)
            .outerjoin(
                MonthlyReport,
                and_(
                    MonthlyReport.hospital_id == latest.c.hospital_id,
                    MonthlyReport.version == latest.c.version,
                    MonthlyReport.period_year == year,
                    MonthlyReport.period_month == month,
                    MonthlyReport.report_type == "MONTHLY",
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
            impact=(
                "지난달 원장 보고서가 생성되지 않았습니다."
                if report_state_value == "MISSING"
                else "생성된 지난달 보고서가 원장 전달 완료로 기록되지 않았습니다."
            ),
            owner=owner_projection(actor),
            sla_due_at=handoff.sla_due_at if handoff else None,
            sla_state="OVERDUE",
            next_action=(
                "보고서를 생성해 주세요."
                if report_state_value == "MISSING"
                else "전달 자료를 검수해 주세요."
            ),
            action=OperationsAction(
                kind="OPEN_REPORT",
                label="보고서 확인",
                method="GET",
                path=f"/hospitals/{hospital.id}/reports",
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
