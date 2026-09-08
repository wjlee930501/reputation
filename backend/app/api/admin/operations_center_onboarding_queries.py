"""Set-based query builder for the operations onboarding queue."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, case, func, or_, select
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
from app.models.handoff import HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun
from app.schemas.operations import (
    OperationsAction,
    OperationsCustomer,
    OperationsHistoryEntry,
    OperationsQueue,
    OperationsQueueRow,
)

#: 인수 처리 기한은 담당 AE의 **수락을 기다리는 동안**에만 뜻이 있다. 수락된 뒤의
#: `sla_due_at`은 이미 지켜진 약속의 기록이므로 기한 초과로 읽으면 안 된다 — 한 화면
#: 계약 등록은 수락 시각을 그대로 기한으로 남기므로, 상태를 보지 않으면 등록된 모든
#: 병원이 온보딩 큐에서 기한 초과·HIGH로 뜬다.
_PENDING_ACCEPTANCE_DUE_AT = case(
    (HospitalHandoff.state == HandoffState.CONTRACTED, HospitalHandoff.sla_due_at),
    else_=None,
)


def _pending_acceptance_due_at(handoff: HospitalHandoff | None) -> datetime | None:
    """`_PENDING_ACCEPTANCE_DUE_AT`의 행 단위 판정 — 두 판정이 갈리면 안 된다."""
    if handoff is None or handoff.state is not HandoffState.CONTRACTED:
        return None
    return handoff.sla_due_at


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
    # 공개 활성화(STEP 5) 뒤에도 자료/Essence/스케줄(STEP 6)이 남는다. ACTIVE만
    # 보고 온보딩 큐에서 제거하면 AE가 콘텐츠 운영 준비를 끝내기 전에 병원이 사라진다.
    # 신규 온보딩의 schedule_set은 서버의 fresh-Essence gate를 통과해야만 True가 된다.
    predicates = [
        Hospital.status != HospitalStatus.PAUSED,
        or_(
            Hospital.status != HospitalStatus.ACTIVE,
            Hospital.schedule_set.is_(False),
        ),
    ]
    if filters.hospital_id is not None:
        predicates.append(Hospital.id == filters.hospital_id)
    owner_filter = owner_predicate(assignee, filters.owner)
    sla_filter = sla_predicate(_PENDING_ACCEPTANCE_DUE_AT, filters.sla, now)
    severity = case(
        (_PENDING_ACCEPTANCE_DUE_AT < now, "HIGH"),
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
        .order_by(_PENDING_ACCEPTANCE_DUE_AT.asc().nullslast(), Hospital.created_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(page_statement)).all())
    if overview:
        total = int(rows[0]._total) if rows else 0
    else:
        total = int((await db.scalar(count_statement)) or 0)

    run_by_hospital: dict = {}
    if rows:
        hospital_ids = [hospital.id for hospital, _handoff, _actor, _total in rows]
        run_rows = (
            await db.execute(
                select(OperationRun.hospital_id, OperationRun.id)
                .where(
                    OperationRun.hospital_id.in_(hospital_ids),
                    OperationRun.operation_type.in_(("TRIGGER_V0_REPORT", "REBUILD_SITE")),
                )
                .order_by(OperationRun.requested_at.desc())
            )
        ).all()
        for hospital_id, run_id in run_rows:
            if hospital_id not in run_by_hospital:
                run_by_hospital[hospital_id] = run_id

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
            if sla_state(_pending_acceptance_due_at(handoff), now) == "OVERDUE"
            else "MEDIUM",
            impact="필수 온보딩이 남아 있어 자동 콘텐츠 운영 준비가 완료되지 않았습니다.",
            owner=owner_projection(actor),
            sla_due_at=_pending_acceptance_due_at(handoff),
            sla_state=sla_state(_pending_acceptance_due_at(handoff), now),
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
            operation_run_id=run_by_hospital.get(hospital.id),
            occurred_at=hospital.updated_at,
        )
        for hospital, handoff, actor, _total in rows
    ]


__all__ = ("load_onboarding_queue",)
