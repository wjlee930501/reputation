"""Incident transitions observed by terminal lead-diagnosis recovery tasks."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_sessionmaker
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    OperationRun,
    OperationRunState,
)
from app.services.incident_safety import build_incident_key
from app.services.incidents import (
    IncidentFingerprint,
    IncidentOpenRequest,
    mark_recovered,
    mark_retrying,
    open_or_touch_incident,
)


def _fingerprint(axis: str) -> IncidentFingerprint:
    return IncidentFingerprint.RENDER_FAILED if axis == "REPORT" else IncidentFingerprint.UNKNOWN


def _request(
    diagnosis_id: uuid.UUID,
    axis: str,
    operation_run_id: uuid.UUID | None,
    hospital_id: uuid.UUID | None,
    error: str | None,
    next_action: str,
) -> IncidentOpenRequest:
    return IncidentOpenRequest(
        pipeline="lead_diagnosis",
        object_type=axis.lower(),
        object_id=str(diagnosis_id),
        fingerprint=_fingerprint(axis),
        incident_type=f"RECOVER_LEAD_{axis}",
        severity=IncidentSeverity.HIGH,
        customer_impact="신청자에게 무료 진단 리포트가 전달되지 않았습니다.",
        source_type="lead_diagnosis",
        source_id=str(diagnosis_id),
        operation_run_id=operation_run_id,
        hospital_id=hospital_id,
        safe_error_message=error,
        next_action=next_action,
        admin_path="/leads",
    )


async def mark_lead_recovery_started(
    db: AsyncSession,
    diagnosis_id: uuid.UUID,
    axis: str,
    run: OperationRun,
    hospital_id: uuid.UUID | None,
    error: str | None,
    actor: str,
    reason: str,
) -> None:
    request = _request(
        diagnosis_id,
        axis,
        run.id,
        hospital_id,
        error,
        "복구 작업 결과를 확인해 주세요.",
    )
    incident = await open_or_touch_incident(db, request, actor=actor, reason=reason)
    retrying = await mark_retrying(
        db, incident.id, expected_version=incident.version, actor=actor, reason=reason
    )
    await db.commit()
    await db.refresh(run)
    current = retrying if isinstance(retrying, Incident) else await db.get(Incident, incident.id)
    if current is None:
        return
    if run.state in {OperationRunState.SUCCEEDED.value, OperationRunState.PARTIAL.value}:
        await mark_recovered(
            db,
            current.id,
            expected_version=current.version,
            observed_success=True,
            actor="system",
            reason="terminal recovery completed before projection",
        )
    elif run.state in {OperationRunState.FAILED.value, OperationRunState.CANCELLED.value}:
        await open_or_touch_incident(
            db, request, actor="system", reason="terminal recovery already failed"
        )
    await db.commit()


async def mark_lead_recovery_failed(
    diagnosis_id: uuid.UUID,
    axis: str,
    operation_run_id: uuid.UUID | None,
    error: str,
) -> None:
    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as db:
        await open_or_touch_incident(
            db,
            _request(
                diagnosis_id,
                axis,
                operation_run_id,
                None,
                error,
                "실패 원인을 확인한 뒤 복구 작업을 다시 실행해 주세요.",
            ),
            actor="system",
            reason="terminal recovery failed",
        )
        await db.commit()


async def mark_lead_recovery_succeeded(diagnosis_id: uuid.UUID, axis: str) -> None:
    key = build_incident_key("lead_diagnosis", axis.lower(), str(diagnosis_id), _fingerprint(axis))
    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as db:
        incident = await db.scalar(select(Incident).where(Incident.dedupe_key == key))
        if incident is None or incident.state != IncidentState.RETRYING.value:
            return
        await mark_recovered(
            db,
            incident.id,
            expected_version=incident.version,
            observed_success=True,
            actor="system",
            reason="terminal recovery completed",
        )
        await db.commit()
