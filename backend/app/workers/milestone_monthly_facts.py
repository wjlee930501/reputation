"""Server-owned monthly report facts shared by milestone projections."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
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


@dataclass(frozen=True, slots=True)
class ReportFacts:
    report: MonthlyReport
    hospital: Hospital
    manifest: MonthlyMeasurementManifest | None
    artifact: MonthlyReportArtifact | None
    artifact_state: ReportArtifactState
    ready: bool
    blockers: tuple[str, ...]


async def load_report_facts(db: AsyncSession) -> dict[uuid.UUID, ReportFacts]:
    rows = (
        await db.execute(
            select(MonthlyReport, Hospital, MonthlyMeasurementManifest, MonthlyReportArtifact)
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
            .where(MonthlyReport.report_type == "MONTHLY")
        )
    ).all()
    facts_by_report: dict[uuid.UUID, ReportFacts] = {}
    for report, hospital, manifest, artifact in rows:
        gate = _delivery_gate(report, manifest, artifact)
        readiness = await get_essence_readiness(db, report.hospital_id)
        current_blockers = _current_essence_delivery_blockers(report, readiness)
        facts_by_report[report.id] = ReportFacts(
            report,
            hospital,
            manifest,
            artifact,
            _artifact_state(report, artifact),
            gate.ready and not current_blockers,
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
