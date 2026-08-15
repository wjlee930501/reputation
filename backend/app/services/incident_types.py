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
