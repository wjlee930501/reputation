"""Shared identifiers and safe notification projection for monthly PDF incidents."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.models.operations import Incident
from app.services.incident_types import incident_type_of
from app.services.notification_contracts import IncidentSlackProjection


@dataclass(frozen=True, slots=True)
class MonthlyArtifactIncidentContext:
    hospital_id: uuid.UUID
    hospital_name: str
    report_id: uuid.UUID
    year: int
    month: int
    operation_run_id: uuid.UUID | None

    @property
    def period_key(self) -> str:
        return f"{self.hospital_id}:{self.year}-{self.month:02d}"

    @property
    def admin_path(self) -> str:
        return f"/hospitals/{self.hospital_id}/reports?report={self.report_id}"


def incident_projection(
    context: MonthlyArtifactIncidentContext,
    incident: Incident,
    *,
    problem: str,
) -> IncidentSlackProjection:
    return IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name=context.hospital_name,
        severity=incident.severity,
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        owner_label="담당 AE",
        sla_label="가능한 빨리",
        hospital_id=context.hospital_id,
        operation_run_id=context.operation_run_id,
        version=incident.version,
        problem=problem,
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )
