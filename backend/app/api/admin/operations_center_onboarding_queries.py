"""Set-based query builder for the operations onboarding queue."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.admin.operations_center_query_common import (
    OperationsFilters,
    owner_predicate,
    sla_predicate,
)
from app.api.admin.operations_center_serializers import (
    next_onboarding_step,
    owner_projection,
    sla_state,
)
from app.models.admin_user import AdminUser
from app.models.handoff import HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.schemas.operations import (
    OperationsAction,
    OperationsCustomer,
    OperationsHistoryEntry,
    OperationsQueue,
    OperationsQueueRow,
)


async def load_onboarding_queue(
    db: AsyncSession,
    filters: OperationsFilters,
    *,
    page: int,
    page_size: int,
    overview: bool,
    now: datetime,
) -> tuple[int, list[OperationsQueueRow]]:
    """Load one onboarding page using one overview query or count plus page queries."""
    assignee = aliased(AdminUser)
    predicates = [Hospital.status.notin_((HospitalStatus.ACTIVE, HospitalStatus.PAUSED))]
    owner_filter = owner_predicate(assignee, filters.owner)
    sla_filter = sla_predicate(HospitalHandoff.sla_due_at, filters.sla, now)
    severity = case(
        (HospitalHandoff.sla_due_at < now, "HIGH"),
        else_="MEDIUM",
    )
    if owner_filter is not None:
        predicates.append(owner_filter)
    if sla_filter is not None:
        predicates.append(sla_filter)
    if filters.status is not None:
        predicates.append(Hospital.status.cast(String) == filters.status)
    if filters.severity is not None:
        predicates.append(severity == filters.severity)

    count_statement = (
        select(func.count(Hospital.id))
        .select_from(Hospital)
        .outerjoin(HospitalHandoff, HospitalHandoff.hospital_id == Hospital.id)
        .outerjoin(assignee, assignee.id == HospitalHandoff.ae_owner_id)
        .where(*predicates)
    )
    page_statement = (
        select(Hospital, HospitalHandoff, assignee, func.count().over().label("_total"))
        .outerjoin(HospitalHandoff, HospitalHandoff.hospital_id == Hospital.id)
        .outerjoin(assignee, assignee.id == HospitalHandoff.ae_owner_id)
        .where(*predicates)
        .order_by(HospitalHandoff.sla_due_at.asc().nullslast(), Hospital.created_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(page_statement)).all())
    if overview:
        total = int(rows[0]._total) if rows else 0
    else:
        total = int((await db.scalar(count_statement)) or 0)

    return total, [
        OperationsQueueRow(
            id=f"onboarding:{hospital.id}",
            queue=OperationsQueue.ONBOARDING,
            customer=OperationsCustomer(
                hospital_id=hospital.id,
                name=hospital.name,
                admin_path=f"/hospitals/{hospital.id}/onboarding",
            ),
            status=hospital.status.value,
            severity="HIGH"
            if sla_state(handoff.sla_due_at if handoff else None, now) == "OVERDUE"
            else "MEDIUM",
            impact="필수 온보딩이 남아 있어 병원 채널의 공개 운영을 시작할 수 없습니다.",
            owner=owner_projection(actor),
            sla_due_at=handoff.sla_due_at if handoff else None,
            sla_state=sla_state(handoff.sla_due_at if handoff else None, now),
            next_action=(
                "운영 센터의 “온보딩 계속”을 눌러 표시된 다음 단계의 저장 또는 승인을 "
                f"완료하세요. 다음 단계: {next_onboarding_step(hospital)} "
                "표시된 조치가 없으면 개발팀에 병원명과 현재 화면의 문구를 전달하세요."
            ),
            action=OperationsAction(
                kind="CONTINUE_ONBOARDING",
                label="온보딩 계속",
                method="GET",
                path=f"/hospitals/{hospital.id}/onboarding",
            ),
            retry=None,
            safe_cause=None,
            history=[OperationsHistoryEntry(event="ONBOARDING_STARTED", at=hospital.created_at)],
            slack=None,
            occurred_at=hospital.updated_at,
        )
        for hospital, handoff, actor, _total in rows
    ]


__all__ = ("load_onboarding_queue",)
