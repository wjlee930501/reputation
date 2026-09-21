"""Server-owned monthly report facts shared by milestone projections."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.reports import (
    _artifact_state,
    _current_essence_delivery_blockers,
    _delivery_gate,
)
from app.models.hospital import Hospital
from app.models.monthly_control import (
    MonthlyMeasurementManifest,
    MonthlyReportArtifact,
    ReportArtifactState,
)
from app.models.report import MonthlyReport
from app.services.essence_readiness import get_essence_readiness
from app.services.monthly_delivery_projection import (
    delivery_is_effective,
    latest_delivery_event_subquery,
)


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
    readiness_by_hospital = {}
    for report, hospital, manifest, artifact, latest_delivery_type in rows:
        gate = _delivery_gate(report, manifest, artifact)
        if report.hospital_id not in readiness_by_hospital:
            readiness_by_hospital[report.hospital_id] = await get_essence_readiness(db, report.hospital_id)
        readiness = readiness_by_hospital[report.hospital_id]
        current_blockers = _current_essence_delivery_blockers(report, readiness)
        facts_by_report[report.id] = ReportFacts(
            report,
            hospital,
            manifest,
            artifact,
            _artifact_state(report, artifact),
            gate.ready and not current_blockers,
            delivery_is_effective(
                latest_event_type=latest_delivery_type,
                legacy_sent_at_present=report.sent_at is not None,
            ),
            tuple(
                item
                for item in (
                    gate.code,
                    "CURRENT_READINESS_BLOCKED" if current_blockers else None,
                )
                if item is not None
            ),
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
