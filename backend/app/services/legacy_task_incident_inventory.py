"""Classify live legacy task incidents without guessing missing identity."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Iterator

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.operations import Incident, IncidentState, OperationRun
from app.services.operation_terminal_outcomes import (
    TerminalOutcomeIdentity,
    terminal_outcome_identity,
)

_LIVE: Final = (IncidentState.OPEN.value, IncidentState.RETRYING.value)
_PERIOD_REQUIRED: Final = frozenset(
    {"GENERATE_MONTHLY_REPORT", "MONTHLY_SOV_PERIOD", "RUN_SOV", "SCHEDULED_MONTHLY_REPORT"}
)
_CONTEXT_REQUIRED: Final = frozenset(
    {
        "GENERATE_CONTENT_ITEM",
        "RECERTIFY_PUBLISHED_IMAGE",
        "REGENERATE_CONTENT",
        "REGENERATE_CONTENT_IMAGE",
    }
)
_SCAN_PAGE_SIZE: Final = 200


@dataclass(frozen=True, slots=True)
class LegacyIncidentInventory:
    convertible_open: int
    unknown_open: int


@dataclass(frozen=True, slots=True)
class LegacyIncidentCandidate:
    incident: Incident
    run: OperationRun
    identity: TerminalOutcomeIdentity


def classify_legacy_incident(
    incident: Incident, run: OperationRun | None
) -> LegacyIncidentCandidate | None:
    if run is None or run.hospital_id is None or incident.hospital_id != run.hospital_id:
        return None
    payload = run.request_payload
    if not isinstance(payload, dict):
        return None
    dispatch = payload.get("_dispatch")
    if not isinstance(dispatch, dict) or not dispatch.get("target_id"):
        return None
    identity = terminal_outcome_identity(run)
    if identity is None:
        return None
    if run.operation_type in _PERIOD_REQUIRED and identity.period is None:
        return None
    if run.operation_type in _CONTEXT_REQUIRED and identity.source_id.endswith("|current"):
        return None
    return LegacyIncidentCandidate(incident=incident, run=run, identity=identity)


def iter_live_legacy_incidents(
    db: Session,
) -> Iterator[tuple[Incident, OperationRun | None]]:
    """Yield keyset pages so unknown history cannot exhaust process memory."""

    cursor_at: datetime | None = None
    cursor_id = uuid.UUID(int=0)
    while True:
        cursor = True
        if cursor_at is not None:
            cursor = or_(
                Incident.created_at > cursor_at,
                (Incident.created_at == cursor_at) & (Incident.id > cursor_id),
            )
        page = tuple(
            db.execute(
                select(Incident, OperationRun)
                .outerjoin(OperationRun, OperationRun.id == Incident.operation_run_id)
                .where(
                    Incident.incident_type == "BACKGROUND_TASK_FAILED",
                    Incident.state.in_(_LIVE),
                    cursor,
                )
                .order_by(Incident.created_at, Incident.id)
                .limit(_SCAN_PAGE_SIZE)
            ).all()
        )
        yield from page
        if len(page) < _SCAN_PAGE_SIZE:
            return
        cursor_at = page[-1][0].created_at
        cursor_id = page[-1][0].id


def inspect_legacy_task_incidents(db: Session) -> LegacyIncidentInventory:
    """Count live legacy rows that can and cannot be mapped without guessing."""

    convertible = 0
    unknown = 0
    for incident, run in iter_live_legacy_incidents(db):
        if classify_legacy_incident(incident, run) is None:
            unknown += 1
        else:
            convertible += 1
    return LegacyIncidentInventory(convertible_open=convertible, unknown_open=unknown)
