"""Final CAS for lead-report artifacts after slow rendering and upload."""

import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead_diagnosis import (
    REPORTABLE_EXECUTION_STATUSES,
    DeliveryStatus,
    LeadDiagnosis,
    LeadReportArtifact,
    ReportStatus,
)
from app.services import lead_report


async def finalize_lead_report_artifact(
    db: AsyncSession,
    diagnosis_id: uuid.UUID,
    claimed_attempt: int,
    artifact: LeadReportArtifact,
) -> bool:
    """Commit only the exact BUILDING claim; delete an upload rejected by a newer state."""
    db.add(artifact)
    ready = (
        await db.execute(
            update(LeadDiagnosis)
            .where(
                LeadDiagnosis.id == diagnosis_id,
                LeadDiagnosis.report_status == ReportStatus.BUILDING.value,
                LeadDiagnosis.report_attempts == claimed_attempt,
                LeadDiagnosis.execution_status.in_(sorted(REPORTABLE_EXECUTION_STATUSES)),
                LeadDiagnosis.delivery_status.notin_(
                    (DeliveryStatus.SENDING.value, DeliveryStatus.SENT.value)
                ),
            )
            .values(report_status=ReportStatus.READY.value)
            .returning(LeadDiagnosis.id)
        )
    ).scalar_one_or_none()
    if ready is None:
        await db.rollback()
        lead_report.delete_report_pdf(artifact.storage_uri)
        return False
    await db.commit()
    return True
