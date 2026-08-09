"""Allowlisted operation-run and Slack outbox retry routes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.operations_center_actions import (
    authorize_run_retry,
    operations_error,
    require_operations_account,
    require_owner,
    retry_policy,
    run_refetch_path,
    scoped_run,
)
from app.api.admin.operations_center_serializers import run_summary, slack_state
from app.core.database import get_db
from app.models.admin_user import ROLE_OWNER, AdminUser
from app.models.operations import Incident, NotificationOutbox
from app.schemas.operations import (
    NotificationRetryRequest,
    OperationRetryRequest,
    OperationsRunSummary,
    OperationsSlackState,
)
from app.services.audit_log import write_audit_log
from app.services.incident_safety import sanitize_operator_text
from app.services.notification_outbox import NotificationRetryConflict, retry_notification
from app.services.operation_run_transitions import OperationTransitionRejected
from app.services.operation_runs import (
    OperationQueueUnavailable,
    RetryCommand,
    retry_operation_run,
)

router = APIRouter()
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=255),
]


@router.post(
    "/hospitals/{hospital_id}/runs/{run_id}/retry",
    response_model=OperationsRunSummary,
)
async def retry_operations_run(
    hospital_id: uuid.UUID,
    run_id: uuid.UUID,
    body: OperationRetryRequest,
    idempotency_key: IdempotencyKeyHeader,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_operations_account),
) -> OperationsRunSummary:
    """Retry one terminal allowlisted command using the durable server dispatch."""
    run = await scoped_run(db, hospital_id, run_id)
    await authorize_run_retry(db, actor, run)
    policy = await retry_policy(db, run)
    try:
        dispatch = await retry_operation_run(
            db,
            RetryCommand(
                run_id=run.id,
                requested_by_id=actor.id,
                audit_actor=actor.email,
                request_key=idempotency_key,
            ),
            policy.task,
        )
    except OperationQueueUnavailable as exc:
        raise operations_error(
            503,
            "BROKER_UNAVAILABLE",
            str(exc),
            operation_run_id=str(exc.run_id),
        ) from exc
    except OperationTransitionRejected as exc:
        raise operations_error(
            409,
            "OPERATION_TRANSITION_CONFLICT",
            "현재 작업 상태에서는 재시도할 수 없습니다.",
            current_version=run.version,
            current_state=run.state,
            refetch_path=run_refetch_path(hospital_id, run_id),
        ) from exc
    if not dispatch.replayed:
        await write_audit_log(
            db,
            action="operation_retry_reason_recorded",
            hospital_id=hospital_id,
            actor=actor.email,
            target_type="operation_run",
            target_id=dispatch.run.id,
            detail={
                "parent_run_id": str(run.id),
                "reason": sanitize_operator_text(body.reason, limit=200),
            },
        )
        await db.commit()
    projection = run_summary(hospital_id, dispatch.run)
    if projection is None:
        raise operations_error(
            409, "OPERATION_RETRY_FAILED", "재시도 실행 기록을 확인할 수 없습니다."
        )
    return projection


async def _notification(
    db: AsyncSession,
    hospital_id: uuid.UUID | None,
    notification_id: uuid.UUID,
) -> NotificationOutbox:
    scope = (
        NotificationOutbox.hospital_id.is_(None)
        if hospital_id is None
        else NotificationOutbox.hospital_id == hospital_id
    )
    row = await db.scalar(
        select(NotificationOutbox).where(
            NotificationOutbox.id == notification_id,
            scope,
        )
    )
    if row is None:
        raise operations_error(
            404, "NOTIFICATION_NOT_FOUND", "해당 범위의 Slack 알림을 찾을 수 없습니다."
        )
    return row


async def _authorize_notification_retry(
    db: AsyncSession,
    actor: AdminUser,
    row: NotificationOutbox,
) -> None:
    if actor.role == ROLE_OWNER:
        return
    assigned = row.incident_id is not None and await db.scalar(
        select(func.count(Incident.id)).where(
            Incident.id == row.incident_id,
            Incident.owner_id == actor.id,
        )
    )
    if not assigned:
        raise operations_error(
            403,
            "ASSIGNEE_OR_OWNER_REQUIRED",
            "담당자로 지정된 운영자만 재시도할 수 있습니다.",
        )


async def _retry_notification(
    db: AsyncSession,
    actor: AdminUser,
    hospital_id: uuid.UUID | None,
    notification_id: uuid.UUID,
    body: NotificationRetryRequest,
) -> OperationsSlackState:
    row = await _notification(db, hospital_id, notification_id)
    if hospital_id is None:
        require_owner(actor)
    else:
        await _authorize_notification_retry(db, actor, row)
    result = await retry_notification(
        db,
        notification_id,
        expected_version=body.expected_version,
        actor=actor.email,
        actor_id=actor.id,
        reason=body.reason,
    )
    if isinstance(result, NotificationRetryConflict):
        status = 404 if result.code == "NOTIFICATION_NOT_FOUND" else 409
        refetch_path = (
            f"/api/admin/operations/incidents/{row.incident_id}"
            if hospital_id is None
            else f"/api/admin/operations/hospitals/{hospital_id}/incidents/{row.incident_id}"
        )
        raise operations_error(
            status,
            result.code,
            "Slack 알림 상태가 변경되었습니다. 최신 상태를 다시 불러와 주세요.",
            current_version=result.current_version,
            current_state=result.current_state,
            refetch_path=refetch_path,
        )
    await db.commit()
    projection = slack_state(result)
    if projection is None:
        raise operations_error(
            409, "NOTIFICATION_RETRY_FAILED", "Slack 알림 재시도 상태를 확인할 수 없습니다."
        )
    return projection


@router.post(
    "/hospitals/{hospital_id}/notifications/{notification_id}/retry",
    response_model=OperationsSlackState,
)
async def retry_operations_notification(
    hospital_id: uuid.UUID,
    notification_id: uuid.UUID,
    body: NotificationRetryRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_operations_account),
) -> OperationsSlackState:
    return await _retry_notification(db, actor, hospital_id, notification_id, body)


@router.post(
    "/notifications/{notification_id}/retry",
    response_model=OperationsSlackState,
)
async def retry_global_notification(
    notification_id: uuid.UUID,
    body: NotificationRetryRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_operations_account),
) -> OperationsSlackState:
    return await _retry_notification(db, actor, None, notification_id, body)


__all__ = (
    "retry_operations_notification",
    "retry_operations_run",
    "router",
)
