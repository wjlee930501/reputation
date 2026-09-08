"""Set-based incident queue reads for the operations center."""

from __future__ import annotations

import uuid
from datetime import datetime
from types import EllipsisType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.api.admin.operations_center_query_common import (
    IncidentRecoveryFilter,
    OperationsFilters,
    owner_predicate,
    sla_predicate,
)
from app.api.admin.operations_center_serializers import (
    canonical_cause_code,
    cost_guard_category,
    requires_operator_action,
    serialize_incident_row,
)
from app.models.admin_user import AdminUser
from app.models.hospital import Hospital
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.schemas.operations import OperationsQueueRow

HospitalScope = uuid.UUID | None | EllipsisType

# 큐의 ACTIVE("조치 필요") 필터가 보는 상태 집합. 목록 건수와 현황 카드가 같은 집합을
# 봐야 두 화면의 예외 수가 갈리지 않는다.
ACTIVE_INCIDENT_STATES = ("OPEN", "RETRYING")


def _group_incident_rows(
    rows: list[tuple[Incident, Hospital | None, AdminUser | None, OperationRun | None, NotificationOutbox | None]],
    now: datetime,
) -> list[OperationsQueueRow]:
    """Collapse repeated symptoms into one stable root-cause projection."""
    grouped: dict[str, list[OperationsQueueRow]] = {}
    for incident, hospital, actor, run, outbox in rows:
        row = serialize_incident_row(incident, hospital, actor, run, outbox, now)
        key = row.cause_group_key or row.cause_code or incident.incident_type
        grouped.setdefault(key, []).append(row)

    projections: list[OperationsQueueRow] = []
    for key, members in grouped.items():
        representative = members[0]
        hospitals = {
            member.customer.hospital_id
            for member in members
            if member.customer.hospital_id is not None
        }
        projections.append(
            representative.model_copy(
                update={
                    "id": f"cause:{key}" if len(members) > 1 else representative.id,
                    "cause_group_key": key,
                    "same_type_count": len(members),
                    "affected_hospital_count": len(hospitals),
                }
            )
        )
    return projections


def _cause_group_key(
    *,
    incident_safe_error_code: str | None,
    incident_type: str,
    source_type: str | None,
    source_id: str | None,
    run_safe_error_code: str | None,
    run_operation_type: str | None,
) -> str:
    """Same grouping key `serialize_incident_row` derives, without building a full row.

    Takes scalars so the lean pass-1 query below can select just the cause-key columns
    instead of hydrating whole `Incident` objects for every match — must stay identical
    to `serialize_incident_row`'s `projected_group_key` or the two passes disagree on
    which incidents share a group.
    """
    projected_code = canonical_cause_code(
        incident_safe_error_code or run_safe_error_code, incident_type
    )
    budget_category = cost_guard_category(
        projected_code,
        incident_type=incident_type,
        source_type=source_type,
        source_id=source_id,
        run_operation_type=run_operation_type,
    )
    return f"{projected_code}:{budget_category}" if budget_category is not None else projected_code


def _hospital_scope_predicate(hospital_scope: HospitalScope) -> ColumnElement[bool] | None:
    """Limit an incident read to one tenant, global incidents, or all tenants."""
    match hospital_scope:
        case EllipsisType():
            return None
        case None:
            return Incident.hospital_id.is_(None)
        case uuid.UUID() as hospital_id:
            return Incident.hospital_id == hospital_id


async def load_incidents_queue(
    db: AsyncSession,
    filters: OperationsFilters,
    *,
    page: int,
    page_size: int,
    overview: bool,
    now: datetime,
    incident_id: uuid.UUID | None = None,
    hospital_scope: HospitalScope = ...,
) -> tuple[int, list[OperationsQueueRow]]:
    """Load one incident page using one page query plus an optional count query."""
    assignee = aliased(AdminUser)
    predicates: list[ColumnElement[bool]] = []
    if filters.hospital_id is not None:
        predicates.append(Incident.hospital_id == filters.hospital_id)
    if incident_id is not None:
        predicates.append(Incident.id == incident_id)
    scope_predicate = _hospital_scope_predicate(hospital_scope)
    if scope_predicate is not None:
        predicates.append(scope_predicate)
    owner_filter = owner_predicate(assignee, filters.owner)
    if owner_filter is not None:
        predicates.append(owner_filter)
    sla_filter = sla_predicate(Incident.sla_due_at, filters.sla, now)
    if sla_filter is not None:
        predicates.append(sla_filter)
    if filters.status is not None:
        predicates.append(Incident.state == filters.status)
    if filters.severity is not None:
        predicates.append(Incident.severity == filters.severity)
    if filters.status is None:
        if filters.recovery == IncidentRecoveryFilter.ACTIVE:
            predicates.append(Incident.state.in_(ACTIVE_INCIDENT_STATES))
        elif filters.recovery == IncidentRecoveryFilter.CONFIRMED:
            predicates.append(Incident.state.in_(("RECOVERED", "ACKNOWLEDGED")))
    order_by = (
        Incident.sla_due_at.asc().nullslast(),
        Incident.last_seen_at.desc(),
        Incident.id,
    )

    # Pass 1 — figure out cause groups with a lean 2-table join (Incident + the
    # OperationRun it may reference; AdminUser is joined only so `owner_filter` above
    # can apply, no columns of it are read). Only the cause-key columns are selected,
    # so a filter matching thousands of incidents costs a scalar tuple each instead of
    # a hydrated ORM object with every column and its identity-map entry. No
    # Hospital/NotificationOutbox here and no Slack/customer projection either — group
    # membership is all this pass needs, and most of it is thrown away by the
    # `list(groups.keys())[start:...]` page slice below.
    group_statement = (
        select(
            Incident.id,
            Incident.safe_error_code,
            Incident.incident_type,
            Incident.source_type,
            Incident.source_id,
            OperationRun.safe_error_code,
            OperationRun.operation_type,
        )
        .select_from(Incident)
        .outerjoin(assignee, assignee.id == Incident.owner_id)
        .outerjoin(OperationRun, OperationRun.id == Incident.operation_run_id)
        .where(*predicates)
        .order_by(*order_by)
    )
    group_rows = (await db.execute(group_statement)).all()

    groups: dict[str, list[uuid.UUID]] = {}
    for (
        row_id,
        incident_code,
        incident_type,
        source_type,
        source_id,
        run_code,
        run_operation_type,
    ) in group_rows:
        key = _cause_group_key(
            incident_safe_error_code=incident_code,
            incident_type=incident_type,
            source_type=source_type,
            source_id=source_id,
            run_safe_error_code=run_code,
            run_operation_type=run_operation_type,
        )
        groups.setdefault(key, []).append(row_id)

    total = len(groups)
    start = (page - 1) * page_size
    page_incident_ids = [
        incident_id
        for key in list(groups.keys())[start : start + page_size]
        for incident_id in groups[key]
    ]
    if not page_incident_ids:
        return total, []

    # Pass 2 — load the full projection (Hospital/AdminUser/OperationRun/
    # NotificationOutbox) only for the incidents whose groups landed on this page,
    # instead of for every incident that matched the filter.
    latest_outbox_id = (
        select(NotificationOutbox.id)
        .where(NotificationOutbox.incident_id == Incident.id)
        .order_by(NotificationOutbox.created_at.desc(), NotificationOutbox.id.desc())
        .correlate(Incident)
        .limit(1)
        .scalar_subquery()
    )
    page_statement = (
        select(
            Incident,
            Hospital,
            assignee,
            OperationRun,
            NotificationOutbox,
        )
        .outerjoin(Hospital, Hospital.id == Incident.hospital_id)
        .outerjoin(assignee, assignee.id == Incident.owner_id)
        .outerjoin(OperationRun, OperationRun.id == Incident.operation_run_id)
        .outerjoin(NotificationOutbox, NotificationOutbox.id == latest_outbox_id)
        .where(Incident.id.in_(page_incident_ids))
        .order_by(*order_by)
    )
    raw_rows = [tuple(row) for row in (await db.execute(page_statement)).all()]
    grouped = _group_incident_rows(raw_rows, now)
    return total, grouped


async def count_operator_incidents(
    db: AsyncSession,
    hospital_ids: list[uuid.UUID],
    *,
    now: datetime,
) -> dict[uuid.UUID, int]:
    """병원별 "지금 사람이 손대야 하는" 예외 수를 한 쿼리로.

    병원 목록이 자기만의 상태 집합으로 세면 목록의 숫자와 현황 화면이 실제로 보여주는
    예외 카드 수가 갈린다. 그래서 세 가지를 현황과 똑같이 쓴다: 상태는 큐의 ACTIVE 필터
    (`ACTIVE_INCIDENT_STATES`), 사람 몫 판정은 큐 행과 같은 `requires_operator_action`,
    묶음은 같은 원인 그룹(`_cause_group_key`)이다 — 같은 원인 다섯 건은 현황에서 카드
    하나이므로 여기서도 1이다. 현황은 그중 앞 `_EXCEPTION_PAGE_SIZE`개만 싣는다.
    """
    if not hospital_ids:
        return {}
    rows = (
        await db.execute(
            select(
                Incident.hospital_id,
                Incident.state,
                Incident.sla_due_at,
                Incident.safe_error_code,
                Incident.incident_type,
                Incident.source_type,
                Incident.source_id,
                OperationRun.safe_error_code,
                OperationRun.operation_type,
            )
            .select_from(Incident)
            .outerjoin(OperationRun, OperationRun.id == Incident.operation_run_id)
            .where(
                Incident.hospital_id.in_(hospital_ids),
                Incident.state.in_(ACTIVE_INCIDENT_STATES),
            )
        )
    ).all()

    groups: dict[uuid.UUID, set[str]] = {}
    for (
        hospital_id,
        state,
        sla_due_at,
        incident_code,
        incident_type,
        source_type,
        source_id,
        run_code,
        run_operation_type,
    ) in rows:
        if not requires_operator_action(state, sla_due_at, now):
            continue
        groups.setdefault(hospital_id, set()).add(
            _cause_group_key(
                incident_safe_error_code=incident_code,
                incident_type=incident_type,
                source_type=source_type,
                source_id=source_id,
                run_safe_error_code=run_code,
                run_operation_type=run_operation_type,
            )
        )
    return {hospital_id: len(keys) for hospital_id, keys in groups.items()}


__all__ = (
    "ACTIVE_INCIDENT_STATES",
    "HospitalScope",
    "count_operator_incidents",
    "load_incidents_queue",
)
