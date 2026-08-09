"""Audited terminal recovery commands for free lead diagnoses."""

import uuid
from enum import StrEnum
from typing import assert_never

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_user import ADMIN_ROLES, AdminUser
from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    DeliveryStatus,
    ExecutionStatus,
    LeadDiagnosis,
    ReportStatus,
)
from app.models.operations import OperationRun, OperationRunState
from app.services.operation_run_keys import normalize_operation_key
from app.services.operation_runs import (
    OperationCommand,
    OperationDispatch,
    OperationQueueUnavailable,
    dispatch_operation,
)
from app.workers.lead_diagnosis_tasks import (
    recover_lead_diagnosis_measurement,
    recover_lead_diagnosis_report,
)
from app.workers.lead_recovery_incidents import mark_lead_recovery_started

_ACTIVE_STATES = (
    OperationRunState.REQUESTED.value,
    OperationRunState.QUEUED.value,
    OperationRunState.RUNNING.value,
)


class RecoveryAxis(StrEnum):
    MEASUREMENT = "MEASUREMENT"
    REPORT = "REPORT"


def _operation_type(axis: RecoveryAxis) -> str:
    match axis:
        case RecoveryAxis.MEASUREMENT:
            return "RECOVER_LEAD_MEASUREMENT"
        case RecoveryAxis.REPORT:
            return "RECOVER_LEAD_REPORT"
        case unreachable:
            assert_never(unreachable)


async def _locked_diagnosis(
    db: AsyncSession, lead_id: uuid.UUID, diagnosis_id: uuid.UUID
) -> tuple[SalesLead, LeadDiagnosis]:
    lead = await db.get(SalesLead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    diagnosis = await db.scalar(
        select(LeadDiagnosis)
        .where(LeadDiagnosis.id == diagnosis_id, LeadDiagnosis.lead_id == lead_id)
        .with_for_update()
    )
    if diagnosis is None:
        raise HTTPException(status_code=404, detail="무료 진단 기록을 찾을 수 없습니다.")
    return lead, diagnosis


def _authorize(actor: AdminUser) -> None:
    if actor.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="복구 작업을 실행할 권한이 없습니다.")


def _ensure_recoverable(diagnosis: LeadDiagnosis, axis: RecoveryAxis) -> int:
    match axis:
        case RecoveryAxis.MEASUREMENT:
            safe_downstream = diagnosis.report_status not in {
                ReportStatus.READY.value,
                ReportStatus.PURGED.value,
            } and diagnosis.delivery_status not in {
                DeliveryStatus.SENDING.value,
                DeliveryStatus.SENT.value,
            }
            if diagnosis.execution_status != ExecutionStatus.FAILED.value or not safe_downstream:
                raise HTTPException(
                    status_code=409,
                    detail="실패로 종료됐고 아직 전달되지 않은 측정만 다시 실행할 수 있습니다.",
                )
            return diagnosis.execution_attempts
        case RecoveryAxis.REPORT:
            measurement_ready = diagnosis.execution_status in {
                ExecutionStatus.SUCCEEDED.value,
                ExecutionStatus.PARTIAL.value,
            }
            delivery_safe = diagnosis.delivery_status not in {
                DeliveryStatus.SENDING.value,
                DeliveryStatus.SENT.value,
            }
            if diagnosis.report_status != ReportStatus.BLOCKED.value or not measurement_ready:
                raise HTTPException(
                    status_code=409,
                    detail="측정이 끝난 뒤 생성 실패로 종료된 리포트만 다시 만들 수 있습니다.",
                )
            if not delivery_safe:
                raise HTTPException(
                    status_code=409,
                    detail="이미 전달 중이거나 전달된 리포트는 자동으로 다시 만들 수 없습니다.",
                )
            return diagnosis.report_attempts
        case unreachable:
            assert_never(unreachable)


def _targets_diagnosis(run: OperationRun, diagnosis_id: uuid.UUID) -> bool:
    return run.request_payload.get("source_id") == str(diagnosis_id)


async def _existing_dispatch(
    db: AsyncSession,
    *,
    diagnosis_id: uuid.UUID,
    operation_type: str,
    actor: AdminUser,
    hospital_id: uuid.UUID | None,
    idempotency_key: str,
) -> OperationDispatch | None:
    runs = list(
        (
            await db.execute(
                select(OperationRun)
                .where(
                    OperationRun.operation_type == operation_type,
                    OperationRun.hospital_id.is_(hospital_id)
                    if hospital_id is None
                    else OperationRun.hospital_id == hospital_id,
                )
                .order_by(OperationRun.created_at.desc())
                .with_for_update()
            )
        ).scalars()
    )
    normalized = normalize_operation_key(idempotency_key)
    for run in runs:
        if not _targets_diagnosis(run, diagnosis_id):
            continue
        if run.requested_by_id == actor.id and run.idempotency_key == normalized:
            return OperationDispatch(run=run, replayed=True)
        if run.state in _ACTIVE_STATES:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "같은 복구 작업이 이미 진행 중입니다.",
                    "operation_run_id": str(run.id),
                },
            )
    return None


async def _dispatch_recovery(
    db: AsyncSession,
    *,
    lead_id: uuid.UUID,
    diagnosis_id: uuid.UUID,
    axis: RecoveryAxis,
    reason: str,
    idempotency_key: str,
    actor: AdminUser,
) -> dict[str, str | bool]:
    _authorize(actor)
    lead, diagnosis = await _locked_diagnosis(db, lead_id, diagnosis_id)
    operation_type = _operation_type(axis)
    existing = await _existing_dispatch(
        db,
        diagnosis_id=diagnosis.id,
        operation_type=operation_type,
        actor=actor,
        hospital_id=lead.converted_hospital_id,
        idempotency_key=idempotency_key,
    )
    if existing is not None:
        return _response(existing)
    expected_attempts = _ensure_recoverable(diagnosis, axis)
    task = (
        recover_lead_diagnosis_measurement
        if axis is RecoveryAxis.MEASUREMENT
        else recover_lead_diagnosis_report
    )
    try:
        dispatch = await dispatch_operation(
            db,
            OperationCommand(
                operation_type=operation_type,
                hospital_id=lead.converted_hospital_id,
                requested_by_id=actor.id,
                idempotency_key=idempotency_key,
                audit_actor=actor.email,
                target_type="lead_diagnosis",
                target_id=str(diagnosis.id),
                queue="leadgen",
                task_args=(str(diagnosis.id), expected_attempts),
            ),
            task,
        )
    except OperationQueueUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": str(exc), "operation_run_id": str(exc.run_id)},
        ) from exc
    await mark_lead_recovery_started(
        db,
        diagnosis.id,
        axis.value,
        dispatch.run,
        lead.converted_hospital_id,
        diagnosis.error,
        actor.email,
        reason,
    )
    return _response(dispatch)


def _response(dispatch: OperationDispatch) -> dict[str, str | bool]:
    return {
        "detail": "복구 작업을 접수했습니다.",
        "operation_run_id": str(dispatch.run.id),
        "operation_state": dispatch.run.state,
        "idempotent_replay": dispatch.replayed,
    }
