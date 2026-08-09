"""Monthly readiness and delivery-correction milestone scan."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import assert_never

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monthly_control import (
    MonthlyDeliveryEvent,
    ReportDeliveryEventType,
)
from app.services.monthly_events import MonthlyEvent, MonthlyEventType, project_monthly_event
from app.services.notification_contracts import NotificationPayloadError
from app.services.notification_milestone_messages import MilestoneProjection
from app.workers.milestone_monthly_facts import ReportFacts, load_report_facts
from app.workers.milestone_projection_support import (
    MilestoneStateScan,
    ProjectionWindow,
    event_uuid,
    in_window,
    state_uuid,
)


@dataclass(frozen=True, slots=True)
class _MonthlyEventRequest:
    facts: ReportFacts
    event_type: MonthlyEventType
    occurred_at: datetime
    event_id: uuid.UUID


async def scan_monthly_milestones(
    db: AsyncSession, window: ProjectionWindow
) -> tuple[MilestoneProjection, ...]:
    """Project report readiness and append-only delivery corrections in the window."""

    facts_by_report = await load_report_facts(db)
    projections = [
        current
        for facts in facts_by_report.values()
        if (current := _project_current(facts, window)) is not None
    ]
    projections.extend(
        await _project_delivery_events(db, facts_by_report, window.start, window.end)
    )
    return tuple(projections)


async def observe_monthly_milestones(
    db: AsyncSession,
    observed_at: datetime,
    previous_states: dict[str, str],
    delivery_since: datetime,
) -> MilestoneStateScan:
    """Return current report transitions plus unseen append-only delivery facts."""

    facts_by_report = await load_report_facts(db)
    current = tuple(
        _project_observed_current(facts, observed_at) for facts in facts_by_report.values()
    )
    states = {key: projection.stable_id for key, projection in current}
    changed = tuple(
        projection
        for key, projection in current
        if previous_states.get(key) != projection.stable_id
    )
    deliveries = await _project_delivery_events(db, facts_by_report, delivery_since, observed_at)
    return MilestoneStateScan((*changed, *deliveries), states)


async def _project_delivery_events(
    db: AsyncSession,
    facts_by_report: dict[uuid.UUID, ReportFacts],
    since: datetime,
    until: datetime,
) -> tuple[MilestoneProjection, ...]:
    deliveries = (
        await db.execute(
            select(MonthlyDeliveryEvent).where(
                MonthlyDeliveryEvent.created_at >= since,
                MonthlyDeliveryEvent.created_at < until,
                MonthlyDeliveryEvent.event_type.in_(("CORRECTED", "RESCINDED", "REDELIVERED")),
            )
        )
    ).scalars()
    projections: list[MilestoneProjection] = []
    for delivery in deliveries:
        facts = facts_by_report.get(delivery.report_id)
        if facts is not None:
            projections.append(
                project_monthly_event(
                    _monthly_event(
                        _MonthlyEventRequest(
                            facts,
                            _delivery_event_type(delivery.event_type),
                            delivery.created_at,
                            delivery.id,
                        )
                    )
                )
            )
    return tuple(projections)


def _project_current(facts: ReportFacts, window: ProjectionWindow) -> MilestoneProjection | None:
    event_type = _current_state(facts)
    occurred_at = _legacy_transition_time(facts, event_type)
    if occurred_at is None or not in_window(occurred_at, window):
        return None
    request = _MonthlyEventRequest(
        facts,
        event_type,
        occurred_at,
        event_uuid(event_type.value, facts.report.id, occurred_at),
    )
    return project_monthly_event(_monthly_event(request))


def _project_observed_current(
    facts: ReportFacts, observed_at: datetime
) -> tuple[str, MilestoneProjection]:
    event_type = _current_state(facts)
    request = _MonthlyEventRequest(
        facts,
        event_type,
        observed_at,
        state_uuid(event_type.value, facts.report.id, _state_fingerprint(facts)),
    )
    return f"monthly:{facts.report.id}", project_monthly_event(_monthly_event(request))


def _current_state(facts: ReportFacts) -> MonthlyEventType:
    report = facts.report
    if facts.ready and facts.artifact is not None:
        return MonthlyEventType.CUSTOMER_READY
    coverage_complete = (
        report.quality == "COMPLETE"
        and report.planned_count > 0
        and report.success_count == report.planned_count
        and report.failed_count == 0
        and facts.manifest is not None
        and facts.manifest.closed_at is not None
    )
    if coverage_complete and facts.artifact_state.value != "VALID":
        return MonthlyEventType.ARTIFACT_VALIDATION_PENDING
    return MonthlyEventType.BLOCKED


def _legacy_transition_time(facts: ReportFacts, event_type: MonthlyEventType) -> datetime | None:
    if event_type is MonthlyEventType.CUSTOMER_READY and facts.artifact is not None:
        return facts.artifact.validated_at or facts.artifact.created_at
    return facts.report.created_at


def _state_fingerprint(facts: ReportFacts) -> str:
    report = facts.report
    artifact_state = facts.artifact_state.value
    artifact_id = str(facts.artifact.id) if facts.artifact is not None else "none"
    manifest_closed = facts.manifest is not None and facts.manifest.closed_at is not None
    return ":".join(
        (
            report.quality,
            str(report.planned_count),
            str(report.success_count),
            str(report.failed_count),
            str(manifest_closed),
            artifact_state,
            artifact_id,
            *facts.blockers,
        )
    )


def _monthly_event(request: _MonthlyEventRequest) -> MonthlyEvent:
    facts = request.facts
    report = facts.report
    artifact = facts.artifact
    return MonthlyEvent(
        request.event_id,
        request.event_type,
        report.id,
        facts.hospital.id,
        facts.hospital.name,
        report.period_year,
        report.period_month,
        report.quality,
        report.planned_count,
        report.success_count,
        report.failed_count,
        facts.manifest is not None and facts.manifest.closed_at is not None,
        facts.artifact_state,
        artifact.id if artifact is not None else None,
        facts.ready,
        facts.blockers,
        "담당 AE",
        None,
        request.occurred_at,
    )


def _delivery_event_type(value: str) -> MonthlyEventType:
    match ReportDeliveryEventType(value):
        case ReportDeliveryEventType.CORRECTED:
            return MonthlyEventType.DELIVERY_CORRECTED
        case ReportDeliveryEventType.RESCINDED:
            return MonthlyEventType.DELIVERY_RESCINDED
        case ReportDeliveryEventType.REDELIVERED:
            return MonthlyEventType.DELIVERY_REDELIVERED
        case ReportDeliveryEventType.DELIVERED:
            raise NotificationPayloadError("DELIVERED_EVENT_NOT_PROJECTED")
        case unreachable:
            assert_never(unreachable)
