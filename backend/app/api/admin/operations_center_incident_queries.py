"""Set-based incident queue reads for the operations center."""

from __future__ import annotations

import uuid
from datetime import datetime
from types import EllipsisType

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.api.admin.operations_center_query_common import (
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
    latest_outbox_id = (
        select(NotificationOutbox.id)
        .where(NotificationOutbox.incident_id == Incident.id)
        .order_by(NotificationOutbox.created_at.desc(), NotificationOutbox.id.desc())
        .correlate(Incident)
        .limit(1)
        .scalar_subquery()
    )
    count_statement = (
        select(func.count(Incident.id))
        .select_from(Incident)
        .outerjoin(assignee, assignee.id == Incident.owner_id)
        .where(*predicates)
    )
    page_statement = (
        select(
            Incident,
            Hospital,
            assignee,
            OperationRun,
            NotificationOutbox,
            func.count().over().label("_total"),
        )
        .outerjoin(Hospital, Hospital.id == Incident.hospital_id)
        .outerjoin(assignee, assignee.id == Incident.owner_id)
        .outerjoin(OperationRun, OperationRun.id == Incident.operation_run_id)
        .outerjoin(NotificationOutbox, NotificationOutbox.id == latest_outbox_id)
        .where(*predicates)
        .order_by(Incident.sla_due_at.asc().nullslast(), Incident.last_seen_at.desc(), Incident.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(page_statement)).all())
    total = (
        (int(rows[0]._total) if rows else 0)
        if overview
        else int((await db.scalar(count_statement)) or 0)
    )
    return total, [
        serialize_incident_row(incident, hospital, actor, run, outbox, now)
        for incident, hospital, actor, run, outbox, _total in rows
    ]


__all__ = ("HospitalScope", "load_incidents_queue")
