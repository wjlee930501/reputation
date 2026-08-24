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
from app.api.admin.operations_center_serializers import serialize_incident_row
from app.models.admin_user import AdminUser
from app.models.hospital import Hospital
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.schemas.operations import OperationsQueueRow

HospitalScope = uuid.UUID | None | EllipsisType


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
            predicates.append(Incident.state.in_(("OPEN", "RETRYING")))
        elif filters.recovery == IncidentRecoveryFilter.CONFIRMED:
            predicates.append(Incident.state.in_(("RECOVERED", "ACKNOWLEDGED")))
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
        .where(*predicates)
        .order_by(Incident.sla_due_at.asc().nullslast(), Incident.last_seen_at.desc(), Incident.id)
    )
    raw_rows = [tuple(row) for row in (await db.execute(page_statement)).all()]
    grouped = _group_incident_rows(raw_rows, now)
    total = len(grouped)
    start = (page - 1) * page_size
    return total, grouped[start : start + page_size]


__all__ = ("HospitalScope", "load_incidents_queue")
