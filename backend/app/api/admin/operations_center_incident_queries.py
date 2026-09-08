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
from app.schemas.operations import OperationsOwner, OperationsQueueRow

HospitalScope = uuid.UUID | None | EllipsisType

# 큐의 ACTIVE("조치 필요") 필터가 보는 상태 집합. 목록 건수와 현황 카드가 같은 집합을
# 봐야 두 화면의 예외 수가 갈리지 않는다.
ACTIVE_INCIDENT_STATES = ("OPEN", "RETRYING")

# 현황 화면이 한 병원에 실을 수 있는 원인 묶음 상한. 병원당 사람 몫 예외는 이보다 훨씬
# 적고, 넘어간다면 카드를 더 그리는 것보다 원인을 정리하는 게 먼저다. 목록 건수는 상한
# 없이 센다 — 카드가 잘렸다고 병원이 가진 예외 수까지 줄여 말하지 않는다.
_OPERATOR_GROUP_CAP = 100

# 인시던트 정렬 — 기한이 임박한 것부터. 원인 묶음의 대표 행도 이 순서의 첫 건이다.
_INCIDENT_ORDER_BY = (
    Incident.sla_due_at.asc().nullslast(),
    Incident.last_seen_at.desc(),
    Incident.id,
)


def _group_incident_rows(
    rows: list[tuple[Incident, Hospital | None, AdminUser | None, OperationRun | None, NotificationOutbox | None]],
    now: datetime,
    actor: AdminUser | None = None,
) -> list[OperationsQueueRow]:
    """Collapse repeated symptoms into one stable root-cause projection."""
    grouped: dict[str, list[OperationsQueueRow]] = {}
    for incident, hospital, owner, run, outbox in rows:
        row = serialize_incident_row(incident, hospital, owner, run, outbox, now, actor=actor)
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
    actor: AdminUser | None = None,
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
    order_by = _INCIDENT_ORDER_BY

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
    return total, await _load_grouped_rows(db, page_incident_ids, now=now, actor=actor)


async def _load_grouped_rows(
    db: AsyncSession,
    incident_ids: list[uuid.UUID],
    *,
    now: datetime,
    actor: AdminUser | None = None,
) -> list[OperationsQueueRow]:
    """Project the named incidents with their related records and collapse the groups."""
    owner = aliased(AdminUser)
    latest_outbox_id = (
        select(NotificationOutbox.id)
        .where(NotificationOutbox.incident_id == Incident.id)
        .order_by(NotificationOutbox.created_at.desc(), NotificationOutbox.id.desc())
        .correlate(Incident)
        .limit(1)
        .scalar_subquery()
    )
    statement = (
        select(
            Incident,
            Hospital,
            owner,
            OperationRun,
            NotificationOutbox,
        )
        .outerjoin(Hospital, Hospital.id == Incident.hospital_id)
        .outerjoin(owner, owner.id == Incident.owner_id)
        .outerjoin(OperationRun, OperationRun.id == Incident.operation_run_id)
        .outerjoin(NotificationOutbox, NotificationOutbox.id == latest_outbox_id)
        .where(Incident.id.in_(incident_ids))
        .order_by(*_INCIDENT_ORDER_BY)
    )
    raw_rows = [tuple(row) for row in (await db.execute(statement)).all()]
    return _group_incident_rows(raw_rows, now, actor)


async def _operator_incident_groups(
    db: AsyncSession,
    hospital_ids: list[uuid.UUID],
    *,
    now: datetime,
) -> dict[uuid.UUID, dict[str, list[uuid.UUID]]]:
    """병원별 "지금 사람이 손대야 하는" 인시던트를 원인 묶음으로 — 한 쿼리로.

    거르기가 묶기보다 먼저다. 순서가 반대면 같은 원인의 자동 복구 중 건이 대표가 되어,
    목록은 1건으로 세는데 현황은 카드를 하나도 만들지 못한다. 세 가지를 큐와 똑같이 쓴다:
    상태는 큐의 ACTIVE 필터(`ACTIVE_INCIDENT_STATES`), 사람 몫 판정은 큐 행과 같은
    `requires_operator_action`, 묶음은 같은 원인 그룹(`_cause_group_key`)이다.
    """
    if not hospital_ids:
        return {}
    rows = (
        await db.execute(
            select(
                Incident.hospital_id,
                Incident.id,
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
            .order_by(*_INCIDENT_ORDER_BY)
        )
    ).all()

    groups: dict[uuid.UUID, dict[str, list[uuid.UUID]]] = {}
    for (
        hospital_id,
        incident_id,
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
        key = _cause_group_key(
            incident_safe_error_code=incident_code,
            incident_type=incident_type,
            source_type=source_type,
            source_id=source_id,
            run_safe_error_code=run_code,
            run_operation_type=run_operation_type,
        )
        groups.setdefault(hospital_id, {}).setdefault(key, []).append(incident_id)
    return groups


async def count_operator_incidents(
    db: AsyncSession,
    hospital_ids: list[uuid.UUID],
    *,
    now: datetime,
) -> dict[uuid.UUID, int]:
    """병원 목록이 싣는 예외 수 — 현황 카드와 같은 파이프라인의 묶음 수다.

    같은 원인 다섯 건은 현황에서 카드 하나이므로 여기서도 1이다.
    """
    return {
        hospital_id: len(keys)
        for hospital_id, keys in (
            await _operator_incident_groups(db, hospital_ids, now=now)
        ).items()
    }


async def load_operator_incident_groups(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    *,
    now: datetime,
    actor: AdminUser | None = None,
) -> list[OperationsQueueRow]:
    """한 병원의 사람 몫 예외 — 원인 묶음 하나가 행 하나다(현황 화면의 예외 카드).

    묶음 수는 `count_operator_incidents`와 같은 `_operator_incident_groups`에서 나오므로
    목록의 예외 수와 현황의 카드 수가 갈릴 수 없다.
    """
    groups = (await _operator_incident_groups(db, [hospital_id], now=now)).get(hospital_id, {})
    incident_ids = [
        incident_id
        for key in list(groups)[:_OPERATOR_GROUP_CAP]
        for incident_id in groups[key]
    ]
    if not incident_ids:
        return []
    return await _load_grouped_rows(db, incident_ids, now=now, actor=actor)


async def load_assignable_accounts(db: AsyncSession) -> list[OperationsOwner]:
    """담당으로 고를 수 있는 계정 — 활성 운영자에서 운영 점검 계정을 뺀 목록.

    배정 라우트가 활성 계정만 받으므로(`INVALID_OWNER`) 여기서 같은 조건을 쓴다.
    운영 점검 계정 제외는 실운영 인원 지표(`utils/production_readiness.py`)와 같은 기준이다.
    """
    accounts = (
        (
            await db.execute(
                select(AdminUser)
                .where(
                    AdminUser.is_active.is_(True),
                    AdminUser.is_operations_test.is_(False),
                )
                .order_by(AdminUser.name.asc(), AdminUser.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [OperationsOwner(id=user.id, name=user.name, email=user.email) for user in accounts]


__all__ = (
    "ACTIVE_INCIDENT_STATES",
    "HospitalScope",
    "count_operator_incidents",
    "load_assignable_accounts",
    "load_incidents_queue",
    "load_operator_incident_groups",
)
