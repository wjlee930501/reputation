"""Durable incident and Slack projection for doctor-PDF validation failures."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.incident_types import (
    IncidentFingerprint,
    IncidentOpenRequest,
)
from app.services.incidents import build_incident_key, open_or_touch_incident
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification
from app.services.report_artifact_validation import DoctorPdfValidationError
from app.workers.monthly_artifact_incident_contracts import (
    MonthlyArtifactIncidentContext,
    incident_projection,
)

_SOURCE_TYPE = "MONTHLY_REPORT_ARTIFACT"


async def record_monthly_artifact_failure(
    context: MonthlyArtifactIncidentContext,
    error: DoctorPdfValidationError,
) -> uuid.UUID:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await _open_and_notify(db, context, error)
        await db.commit()
        return incident.id


async def ensure_monthly_artifact_failures(
    contexts: Sequence[MonthlyArtifactIncidentContext],
    error: DoctorPdfValidationError,
) -> list[tuple[uuid.UUID, bool]]:
    """Repair one bounded batch in one database session."""

    return await ensure_monthly_artifact_failure_batch(
        tuple((context, error) for context in contexts)
    )


async def ensure_monthly_artifact_failure_batch(
    failures: Sequence[tuple[MonthlyArtifactIncidentContext, DoctorPdfValidationError]],
) -> list[tuple[uuid.UUID, bool]]:
    sessions = get_async_sessionmaker()
    results: list[tuple[uuid.UUID, bool]] = []
    async with sessions() as db:
        for context, error in failures:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"{_SOURCE_TYPE}:{context.period_key}"},
            )
            existing = (
                await db.execute(
                    select(Incident).where(
                        Incident.hospital_id == context.hospital_id,
                        Incident.source_type == _SOURCE_TYPE,
                        Incident.source_id == context.period_key,
                        Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                results.append((existing.id, False))
                continue
            incident = await _open_and_notify(db, context, error)
            results.append((incident.id, True))
        await db.commit()
    return results


def _fingerprint(code: str) -> IncidentFingerprint:
    if code in {"DOCTOR_PDF_RENDER_FAILED", "DOCTOR_PDF_FONT_NOT_EMBEDDED"}:
        return IncidentFingerprint.RENDER_FAILED
    if code == "DOCTOR_PDF_STORAGE_FAILED":
        return IncidentFingerprint.DELIVERY_FAILED
    return IncidentFingerprint.VALIDATION_FAILED


async def _open_and_notify(
    db: AsyncSession,
    context: MonthlyArtifactIncidentContext,
    error: DoctorPdfValidationError,
) -> Incident:
    previous = await db.scalar(
        select(Incident).where(
            Incident.dedupe_key
            == build_incident_key(
                "monthly_report_artifact",
                "hospital_period",
                context.period_key,
                _fingerprint(error.code),
            )
        )
    )
    previous_state = previous.state if previous is not None else None
    incident = await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="monthly_report_artifact",
            object_type="hospital_period",
            object_id=context.period_key,
            fingerprint=_fingerprint(error.code),
            incident_type="MONTHLY_DOCTOR_PDF_BLOCKED",
            severity=IncidentSeverity.HIGH,
            customer_impact=error.customer_impact,
            source_type=_SOURCE_TYPE,
            next_action=error.next_action,
            admin_path=context.admin_path,
            hospital_id=context.hospital_id,
            operation_run_id=context.operation_run_id,
            source_id=context.period_key,
            safe_error_code=error.code,
            safe_error_message=error.problem,
        ),
        actor="monthly-report-worker",
        reason="원장 전달용 PDF 검증 실패",
    )
    if previous_state is None or previous_state in {
        IncidentState.RECOVERED.value,
        IncidentState.ACKNOWLEDGED.value,
    }:
        await enqueue_notification(
            db,
            build_open_incident_notification(
                incident_projection(context, incident, problem=error.problem),
                settings.ADMIN_BASE_URL,
            ),
        )
    return incident
