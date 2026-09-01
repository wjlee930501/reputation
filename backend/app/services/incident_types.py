"""Typed contracts for the durable incident service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from app.models.operations import Incident, IncidentSeverity, IncidentState


class IncidentFingerprint(StrEnum):
    """Closed, non-volatile causes used in incident identity."""

    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    RATE_LIMITED = "RATE_LIMITED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    COST_BLOCKED = "COST_BLOCKED"
    MISSING_PREREQUISITE = "MISSING_PREREQUISITE"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    DELIVERY_OUTCOME_UNKNOWN = "DELIVERY_OUTCOME_UNKNOWN"
    RENDER_FAILED = "RENDER_FAILED"
    DOMAIN_UNHEALTHY = "DOMAIN_UNHEALTHY"
    CACHE_REVALIDATION_FAILED = "CACHE_REVALIDATION_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    UNKNOWN = "UNKNOWN"


class IncidentAudience(StrEnum):
    """Who can actually act on an incident type.

    ``OPERATOR`` incidents describe customer-visible work an AE can finish in Admin.
    ``DEVELOPER`` incidents describe infrastructure an AE cannot repair (broker,
    background task crashes, notification transport, cache refresh, PDF rendering).
    Routing those to a separate webhook keeps the AE channel actionable.
    """

    OPERATOR = "operator"
    DEVELOPER = "developer"


SLACK_CHANNEL = "SLACK"
SLACK_DEVELOPER_CHANNEL = "SLACK_DEV"

# Only pure-infrastructure incident types are registered here. Every type absent from
# this set defaults to ``OPERATOR`` so a new incident is never silently hidden from
# the AE channel.
_DEVELOPER_INCIDENT_TYPES: frozenset[str] = frozenset(
    {
        "BACKGROUND_TASK_FAILED",
        "BROKER_UNAVAILABLE",
        "UNSAFE_STORED_DISPATCH",
        "NOTIFICATION_DELIVERY_FAILED",
        "NOTIFICATION_DELIVERY_UNKNOWN",
        "CACHE_REVALIDATION_FAILED",
        "MONTHLY_DOCTOR_PDF_BLOCKED",
    }
)


def incident_audience(incident_type: str | None) -> IncidentAudience:
    """Return the audience registered for one incident type (default: operator)."""

    if not incident_type:
        return IncidentAudience.OPERATOR
    if incident_type.strip().upper() in _DEVELOPER_INCIDENT_TYPES:
        return IncidentAudience.DEVELOPER
    return IncidentAudience.OPERATOR


def incident_type_of(incident: object) -> str:
    """Read an incident row's registry key defensively.

    Legacy projections and lightweight adapters can hand over objects without the
    column. An unknown type simply routes to the operator channel.
    """

    value = getattr(incident, "incident_type", "")
    return value if isinstance(value, str) else ""


def notification_channel_for_incident_type(incident_type: str | None) -> str:
    """Map one incident type onto the outbox channel that will carry it."""

    if incident_audience(incident_type) is IncidentAudience.DEVELOPER:
        return SLACK_DEVELOPER_CHANNEL
    return SLACK_CHANNEL


@dataclass(frozen=True, slots=True)
class IncidentOpenRequest:
    pipeline: str
    object_type: str
    object_id: str
    fingerprint: IncidentFingerprint
    incident_type: str
    severity: IncidentSeverity
    customer_impact: str
    source_type: str
    next_action: str
    admin_path: str
    hospital_id: uuid.UUID | None = None
    operation_run_id: uuid.UUID | None = None
    source_id: str | None = None
    safe_error_code: str | None = None
    safe_error_message: str | None = None


@dataclass(frozen=True, slots=True)
class IncidentNotFound:
    code: str
    incident_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class IncidentVersionConflict:
    code: str
    incident_id: uuid.UUID
    expected_version: int
    current_version: int
    current_state: str


@dataclass(frozen=True, slots=True)
class IncidentTransitionConflict:
    code: str
    incident_id: uuid.UUID
    current_version: int
    current_state: str
    required_state: str


IncidentMutationResult: TypeAlias = (
    Incident | IncidentNotFound | IncidentVersionConflict | IncidentTransitionConflict
)


@dataclass(frozen=True, slots=True)
class IncidentFilters:
    states: tuple[IncidentState, ...] = ()
    severities: tuple[IncidentSeverity, ...] = ()
    owner_id: uuid.UUID | None = None
    due_before: datetime | None = None
    overdue_only: bool = False


@dataclass(frozen=True, slots=True)
class IncidentLabels:
    state_label: str
    severity_label: str
    ownership_label: str
    sla_label: str
    next_action: str
    admin_path: str
    requires_operator_action: bool
    # RETRYING rows stay in the queue as a collapsed secondary state: automatic
    # recovery already owns them, so they are context, not work.
    automatic_recovery_in_progress: bool = False
