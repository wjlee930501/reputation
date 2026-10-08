"""Bounded conversion of live run-level incidents to domain outcome identity."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.operations import Incident, IncidentState, OperationRun, OperationRunState
from app.services.legacy_task_incident_inventory import (
    LegacyIncidentCandidate,
    classify_legacy_incident,
    iter_live_legacy_incidents,
)
from app.services.operation_terminal_outcomes import terminal_outcome_identity

_RECONCILIATION_LOCK: Final = 8_515_202_610_09
_SUCCESS_PAGE_SIZE: Final = 100


@dataclass(frozen=True, slots=True)
class LegacyIncidentReconciliation:
    converted: int
    superseded: int
    recovered: int
    unknown: int


def _audit(
    db: Session,
    incident: Incident,
    action: str,
    *,
    detail: dict[str, str | int | bool | None],
) -> None:
    db.add(
        AdminAuditLog(
            hospital_id=incident.hospital_id,
            actor="system:legacy-incident-reconciliation",
            action=action,
            target_type="incident",
            target_id=str(incident.id),
            detail=detail,
        )
    )


def _acknowledge_superseded(
    db: Session,
    incident: Incident,
    *,
    action: str,
    canonical_id: uuid.UUID | None,
) -> None:
    now = datetime.now(UTC)
    legacy_dedupe_key = incident.dedupe_key
    legacy_source_id = incident.source_id
    incident.state = IncidentState.ACKNOWLEDGED.value
    incident.acknowledged_at = now
    incident.acknowledged_by_id = None
    incident.version += 1
    incident.last_seen_at = now
    incident.updated_at = now
    _audit(
        db,
        incident,
        action,
        detail={
            "canonical_incident_id": str(canonical_id) if canonical_id else None,
            "legacy_dedupe_key": legacy_dedupe_key,
            "legacy_incident_type": "BACKGROUND_TASK_FAILED",
            "legacy_operation_run_id": str(incident.operation_run_id),
            "legacy_source_id": legacy_source_id,
            "notifications_enqueued": False,
        },
    )


def _recover_without_notification(db: Session, incident: Incident) -> None:
    now = datetime.now(UTC)
    legacy_dedupe_key = incident.dedupe_key
    legacy_source_id = incident.source_id
    if incident.state == IncidentState.OPEN.value:
        incident.state = IncidentState.RETRYING.value
        incident.version += 1
    incident.state = IncidentState.RECOVERED.value
    incident.recovered_at = now
    incident.version += 1
    incident.state = IncidentState.ACKNOWLEDGED.value
    incident.acknowledged_at = now
    incident.acknowledged_by_id = None
    incident.version += 1
    incident.last_seen_at = now
    incident.updated_at = now
    _audit(
        db,
        incident,
        "legacy_operation_incident_recovered",
        detail={
            "legacy_incident_type": "BACKGROUND_TASK_FAILED",
            "legacy_dedupe_key": legacy_dedupe_key,
            "legacy_operation_run_id": str(incident.operation_run_id),
            "legacy_source_id": legacy_source_id,
            "notifications_enqueued": False,
        },
    )


def _same_outcome_succeeded(db: Session, candidate: LegacyIncidentCandidate) -> bool:
    cursor_at = candidate.run.requested_at
    cursor_id = uuid.UUID(int=0)
    while True:
        page = tuple(
            db.scalars(
                select(OperationRun)
                .where(
                    OperationRun.hospital_id == candidate.run.hospital_id,
                    OperationRun.state == OperationRunState.SUCCEEDED.value,
                    or_(
                        OperationRun.requested_at > cursor_at,
                        (
                            (OperationRun.requested_at == cursor_at)
                            & (OperationRun.id > cursor_id)
                        ),
                    ),
                )
                .order_by(OperationRun.requested_at, OperationRun.id)
                .limit(_SUCCESS_PAGE_SIZE)
            )
        )
        if any(
            (identity := terminal_outcome_identity(run)) is not None
            and identity.dedupe_key == candidate.identity.dedupe_key
            for run in page
        ):
            return True
        if len(page) < _SUCCESS_PAGE_SIZE:
            return False
        cursor_at = page[-1].requested_at
        cursor_id = page[-1].id


def reconcile_legacy_task_incidents(
    db: Session, *, limit: int = 50
) -> LegacyIncidentReconciliation:
    """Convert one bounded batch without deleting incidents, runs, audits, or outbox."""

    db.scalar(select(func.pg_advisory_xact_lock(_RECONCILIATION_LOCK)))
    candidates: list[LegacyIncidentCandidate] = []
    unknown = 0
    for incident, run in iter_live_legacy_incidents(db):
        candidate = classify_legacy_incident(incident, run)
        if candidate is None:
            unknown += 1
        else:
            candidates.append(candidate)
    selected_ids = tuple(candidate.incident.id for candidate in candidates[:limit])
    if selected_ids:
        db.scalars(
            select(Incident)
            .where(Incident.id.in_(selected_ids))
            .with_for_update(of=Incident)
        ).all()
    candidates = candidates[:limit]

    converted = superseded = recovered = 0
    grouped: dict[str, list[LegacyIncidentCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.identity.dedupe_key, []).append(candidate)

    for dedupe_key, group in grouped.items():
        unresolved: list[LegacyIncidentCandidate] = []
        for candidate in group:
            if _same_outcome_succeeded(db, candidate):
                _recover_without_notification(db, candidate.incident)
                recovered += 1
            else:
                unresolved.append(candidate)
        if not unresolved:
            continue

        canonical = db.scalar(
            select(Incident).where(
                Incident.dedupe_key == dedupe_key,
                Incident.incident_type == "OPERATION_TERMINAL_FAILED",
            )
        )
        remaining = unresolved
        if canonical is None:
            first, *remaining = unresolved
            incident = first.incident
            old_key = incident.dedupe_key
            old_source_id = incident.source_id
            incident.dedupe_key = first.identity.dedupe_key
            incident.incident_type = "OPERATION_TERMINAL_FAILED"
            incident.source_type = "OPERATION_OUTCOME"
            incident.source_id = first.identity.source_id
            incident.safe_error_code = first.identity.cause
            incident.admin_path = "/operations"
            incident.version += 1
            incident.updated_at = datetime.now(UTC)
            _audit(
                db,
                incident,
                "legacy_operation_incident_converted",
                detail={
                    "legacy_dedupe_key": old_key,
                    "legacy_incident_type": "BACKGROUND_TASK_FAILED",
                    "legacy_operation_run_id": str(incident.operation_run_id),
                    "legacy_source_id": old_source_id,
                    "notifications_enqueued": False,
                },
            )
            canonical = incident
            converted += 1
        elif canonical.state in {
            IncidentState.RECOVERED.value,
            IncidentState.ACKNOWLEDGED.value,
        }:
            canonical.state = IncidentState.OPEN.value
            canonical.recovered_at = None
            canonical.acknowledged_at = None
            canonical.acknowledged_by_id = None
            canonical.episode_seq += 1
            canonical.occurrence_count += 1
            canonical.version += 1
            canonical.updated_at = datetime.now(UTC)
            _audit(
                db,
                canonical,
                "legacy_operation_incident_reopened",
                detail={
                    "legacy_incident_id": str(group[0].incident.id),
                    "notifications_enqueued": False,
                },
            )
        for candidate in remaining:
            _acknowledge_superseded(
                db,
                candidate.incident,
                action="legacy_operation_incident_superseded",
                canonical_id=canonical.id,
            )
            superseded += 1

    db.commit()
    return LegacyIncidentReconciliation(
        converted=converted,
        superseded=superseded,
        recovered=recovered,
        unknown=unknown,
    )
