"""Server-owned monthly report facts shared by milestone projections."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hospital import Hospital
from app.models.monthly_control import (
    MonthlyMeasurementManifest,
    MonthlyReportArtifact,
    ReportArtifactState,
)
from app.models.report import MonthlyReport
from app.services.monthly_delivery_projection import (
    delivery_is_effective,
    latest_delivery_event_subquery,
)
from app.services.monthly_report_delivery import monthly_report_delivery_gate
from app.services.report_artifact_validation import validate_persisted_doctor_artifact


@dataclass(frozen=True, slots=True)
class ReportFacts:
    report: MonthlyReport
    hospital: Hospital
    manifest: MonthlyMeasurementManifest | None
    artifact: MonthlyReportArtifact | None
    artifact_state: ReportArtifactState
    ready: bool
    delivered: bool
    blockers: tuple[str, ...]


async def load_report_facts(db: AsyncSession) -> dict[uuid.UUID, ReportFacts]:
    latest_delivery = latest_delivery_event_subquery()
    rows = (
        await db.execute(
            select(
                MonthlyReport,
                Hospital,
                MonthlyMeasurementManifest,
                MonthlyReportArtifact,
                latest_delivery.c.event_type,
            )
            .join(Hospital, Hospital.id == MonthlyReport.hospital_id)
            .outerjoin(
                MonthlyMeasurementManifest,
                MonthlyMeasurementManifest.id == MonthlyReport.manifest_id,
            )
            .outerjoin(
                MonthlyReportArtifact,
                (MonthlyReportArtifact.report_id == MonthlyReport.id)
                & (MonthlyReportArtifact.audience == "DOCTOR"),
            )
            .outerjoin(
                latest_delivery,
                and_(
                    latest_delivery.c.report_id == MonthlyReport.id,
                    latest_delivery.c.rn == 1,
                ),
            )
            .where(MonthlyReport.report_type == "MONTHLY")
        )
    ).all()
    facts_by_report: dict[uuid.UUID, ReportFacts] = {}
    for report, hospital, manifest, artifact, latest_delivery_type in rows:
        gate = monthly_report_delivery_gate(report, manifest, artifact)
        artifact_result = validate_persisted_doctor_artifact(report, artifact)
        facts_by_report[report.id] = ReportFacts(
            report,
            hospital,
            manifest,
            artifact,
            ReportArtifactState(artifact_result.state),
            gate.ready,
            delivery_is_effective(
                latest_event_type=latest_delivery_type,
                legacy_sent_at_present=report.sent_at is not None,
            ),
            (gate.code,) if gate.code is not None else (),
        )
    return facts_by_report


def latest_report_facts(facts_by_report: dict[uuid.UUID, ReportFacts]) -> tuple[ReportFacts, ...]:
    """One current report per hospital AND contract month; keep old delivery lookup intact."""
    latest = {}
    for facts in facts_by_report.values():
        report = facts.report
        key = (facts.hospital.id, report.period_year, report.period_month)
        rank = (getattr(report, "version", 0) or 0, report.created_at, str(report.id))
        previous = latest.get(key)
        if previous is None or rank > previous[0]:
            latest[key] = (rank, facts)
    return tuple(value[1] for key, value in sorted(latest.items(), key=lambda item: str(item[0])))
