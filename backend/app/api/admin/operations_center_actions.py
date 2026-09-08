"""Authorization and retry guards shared by operations-center action routes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, assert_never

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.accounts import require_active_account
from app.core.database import get_db
from app.models.admin_user import ROLE_OWNER, AdminUser
from app.models.content import ContentItem
from app.models.operations import Incident, OperationRun
from app.services import operation_run_payloads
from app.services import published_image_recertification as recertification
from app.services.incident_types import (
    IncidentNotFound,
    IncidentTransitionConflict,
    IncidentVersionConflict,
)
from app.services.operation_runs import DispatchTask
from app.workers.tasks import (
    build_aeo_site,
    generate_content_image,
    generate_monthly_report_for_hospital,
    recertify_published_content_image,
    regenerate_content_item,
    run_sov_for_hospital,
    trigger_v0_report,
)

_BFF_OPERATIONS_PREFIX: Final = "/api/admin/operations"


@dataclass(frozen=True, slots=True)
class _TaskPolicy:
    """The only Celery task shape an operations retry may dispatch."""

    task: DispatchTask
    queue: str
    target_type: str
    arg_count: int
    allow_rebuild_true_arg: bool = False


_TASK_POLICIES: Final[dict[str, _TaskPolicy]] = {
    "TRIGGER_V0_REPORT": _TaskPolicy(trigger_v0_report, "reports", "hospital", 1),
    "RUN_SOV": _TaskPolicy(run_sov_for_hospital, "sov", "hospital", 1),
    "REBUILD_SITE": _TaskPolicy(build_aeo_site, "default", "hospital", 1),
    "GENERATE_MONTHLY_REPORT": _TaskPolicy(
        generate_monthly_report_for_hospital, "reports", "hospital", 3, True
    ),
    "REGENERATE_CONTENT": _TaskPolicy(regenerate_content_item, "content", "content_item", 1),
    "REGENERATE_CONTENT_IMAGE": _TaskPolicy(generate_content_image, "content", "content_item", 1),
    "RECERTIFY_PUBLISHED_IMAGE": _TaskPolicy(
        recertify_published_content_image, "content", "content_item", 1
    ),
}


def operations_error(
    status_code: int, code: str, message: str, **context: str | int | None
) -> HTTPException:
    """Return one safe, structured operations-center HTTP error."""

    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **context},
    )


async def require_operations_account(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Require an active Admin actor without exposing account-resolution details."""

    try:
        return await require_active_account(request, db)
    except HTTPException as exc:
        raise operations_error(
            403, "ACTIVE_ACCOUNT_REQUIRED", "활성 운영자 계정으로 다시 로그인해 주세요."
        ) from exc


def require_owner(actor: AdminUser) -> None:
    if actor.role != ROLE_OWNER:
        raise operations_error(403, "OWNER_REQUIRED", "이 작업은 OWNER 권한이 필요합니다.")


def require_assignee_or_owner(actor: AdminUser, incident: Incident) -> None:
    if actor.role != ROLE_OWNER and incident.owner_id != actor.id:
        raise operations_error(
            403,
            "ASSIGNEE_OR_OWNER_REQUIRED",
            "담당자로 지정된 운영자만 처리할 수 있습니다.",
        )


async def scoped_incident(
    db: AsyncSession, hospital_id: uuid.UUID, incident_id: uuid.UUID
) -> Incident:
    """Load an incident only when it belongs to the requested hospital."""

    incident = await db.scalar(
        select(Incident).where(Incident.id == incident_id, Incident.hospital_id == hospital_id)
    )
    if incident is None:
        raise operations_error(
            404, "INCIDENT_NOT_FOUND", "해당 고객의 운영 이슈를 찾을 수 없습니다."
        )
    return incident


async def scoped_run(db: AsyncSession, hospital_id: uuid.UUID, run_id: uuid.UUID) -> OperationRun:
    """Load an operation run only when it belongs to the requested hospital."""

    run = await db.scalar(
        select(OperationRun).where(
            OperationRun.id == run_id,
            OperationRun.hospital_id == hospital_id,
        )
    )
    if run is None:
        raise operations_error(
            404,
            "OPERATION_RUN_NOT_FOUND",
            "해당 고객의 작업 실행 기록을 찾을 수 없습니다.",
        )
    return run


def incident_refetch_path(hospital_id: uuid.UUID | None, incident_id: uuid.UUID) -> str:
    """Return the BFF route used by browser clients after a CAS conflict."""

    if hospital_id is None:
        return f"{_BFF_OPERATIONS_PREFIX}/incidents/{incident_id}"
    return f"{_BFF_OPERATIONS_PREFIX}/hospitals/{hospital_id}/incidents/{incident_id}"


def run_refetch_path(hospital_id: uuid.UUID, run_id: uuid.UUID) -> str:
    """Return the BFF route used by browser clients after a run conflict."""

    return f"{_BFF_OPERATIONS_PREFIX}/hospitals/{hospital_id}/runs/{run_id}"


def raise_incident_conflict(
    result: IncidentNotFound | IncidentVersionConflict | IncidentTransitionConflict,
    hospital_id: uuid.UUID | None,
) -> None:
    """Map a compare-and-swap outcome to a safe client-refetch response."""

    refetch_path = incident_refetch_path(hospital_id, result.incident_id)
    match result:
        case IncidentNotFound():
            raise operations_error(404, result.code, "운영 이슈를 찾을 수 없습니다.")
        case IncidentVersionConflict():
            raise operations_error(
                409,
                result.code,
                "다른 운영자가 먼저 변경했습니다. 최신 상태를 다시 불러와 주세요.",
                current_version=result.current_version,
                current_state=result.current_state,
                refetch_path=refetch_path,
            )
        case IncidentTransitionConflict():
            raise operations_error(
                409,
                result.code,
                "현재 상태에서는 요청한 작업을 수행할 수 없습니다.",
                current_version=result.current_version,
                current_state=result.current_state,
                required_state=result.required_state,
                refetch_path=refetch_path,
            )
        case unreachable:
            assert_never(unreachable)


async def run_retry_enabled(
    db: AsyncSession, actor: AdminUser | None, run: OperationRun
) -> bool:
    """이 요청자가 이 작업을 다시 시도할 수 있는가 — 재시도 라우트와 같은 판정.

    요청자를 모르는 호출(기존 경로)에서는 예전처럼 열어 둔다. 화면이 이 값을 그대로
    버튼의 활성 여부로 쓰므로, 서버 인가와 갈리면 눌러야 알 수 있는 403이 된다.
    """

    if actor is None or actor.role == ROLE_OWNER:
        return True
    return bool(
        await db.scalar(
            select(func.count(Incident.id)).where(
                Incident.operation_run_id == run.id,
                Incident.owner_id == actor.id,
            )
        )
    )


async def authorize_run_retry(db: AsyncSession, actor: AdminUser, run: OperationRun) -> None:
    """Allow a retry to an owner or the assignee of the linked incident."""

    if not await run_retry_enabled(db, actor, run):
        raise operations_error(
            403,
            "ASSIGNEE_OR_OWNER_REQUIRED",
            "담당자로 지정된 운영자만 재시도할 수 있습니다.",
        )


async def require_retry_within_budget(db: AsyncSession, run: OperationRun) -> None:
    """Hold an operator retry to the same rule the automatic paths follow.

    공개 이미지 재인증은 (글, 이미지 subject)당 유료 재검수 예산이 하나뿐이다. 사람이
    버튼을 눌러 그 예산 밖에서 같은 답을 다시 사게 두면 자동 경로의 상한이 무의미해진다.
    """

    if run.operation_type != recertification.RECERTIFY_OPERATION:
        return
    subject = recertification.payload_subject_hash(run)
    if subject is None or run.safe_error_code in recertification.OPERATOR_REQUIRED_CODES:
        raise operations_error(
            409,
            "OPERATION_NOT_RETRYABLE",
            f"자동 재인증이 사람의 결정을 기다리는 상태입니다. {recertification.OPERATOR_ACTION}",
        )
    source_id = recertification.payload_source_id(run)
    runs = (
        (
            await db.execute(
                select(OperationRun).where(
                    OperationRun.hospital_id == run.hospital_id,
                    OperationRun.operation_type == recertification.RECERTIFY_OPERATION,
                )
            )
        )
        .scalars()
        .all()
    )
    item_runs = [
        candidate
        for candidate in runs
        if recertification.payload_source_id(candidate) == source_id
    ]
    now = datetime.now(UTC)
    if recertification.in_flight(item_runs, subject, now=now):
        raise operations_error(
            409,
            "OPERATION_NOT_RETRYABLE",
            "같은 글의 자동 재인증이 진행 중입니다. 완료된 뒤 다시 확인하세요.",
        )
    if (
        recertification.attempts_spent(item_runs, subject, now=now)
        >= recertification.ATTEMPT_BUDGET
    ):
        raise operations_error(
            409,
            "OPERATION_NOT_RETRYABLE",
            f"이 제목의 자동 재인증을 정해진 횟수만큼 이미 시도했습니다. "
            f"{recertification.OPERATOR_ACTION}",
        )


async def retry_policy(db: AsyncSession, run: OperationRun) -> _TaskPolicy:
    """Validate persisted dispatch data against its task and tenant allowlists."""

    policy = _TASK_POLICIES.get(run.operation_type)
    if policy is None:
        raise operations_error(
            422,
            "OPERATION_NOT_RETRYABLE",
            "이 작업 유형은 운영 센터에서 재시도할 수 없습니다.",
        )
    try:
        dispatch = operation_run_payloads.parse_stored_dispatch(
            run.request_payload.get("_dispatch")
        )
    except operation_run_payloads.UnsafeDispatchPayload as exc:
        raise operations_error(
            422,
            "UNSAFE_STORED_DISPATCH",
            "저장된 작업 정보가 안전한 재시도 규격과 맞지 않습니다.",
        ) from exc
    dispatch_matches = (
        dispatch.queue == policy.queue
        and dispatch.target_type == policy.target_type
        and _args_match_policy(dispatch, policy)
    )
    if not dispatch_matches or run.hospital_id is None:
        raise operations_error(
            422,
            "UNSAFE_STORED_DISPATCH",
            "저장된 작업 정보가 허용 목록과 맞지 않습니다.",
        )
    match policy.target_type:
        case "hospital":
            target_matches = dispatch.target_id == str(run.hospital_id)
        case "content_item":
            target_matches = bool(
                await db.scalar(
                    select(func.count(ContentItem.id)).where(
                        ContentItem.id == uuid.UUID(dispatch.target_id),
                        ContentItem.hospital_id == run.hospital_id,
                    )
                )
            )
        case unreachable:
            assert_never(unreachable)
    if not target_matches:
        raise operations_error(
            422,
            "UNSAFE_STORED_DISPATCH",
            "저장된 작업 정보가 허용 목록과 맞지 않습니다.",
        )
    return policy


def _args_match_policy(
    dispatch: operation_run_payloads.DispatchPayload,
    policy: _TaskPolicy,
) -> bool:
    args = dispatch.task_args
    if not args or args[0] != dispatch.target_id:
        return False
    if len(args) == policy.arg_count:
        return True
    return (
        policy.allow_rebuild_true_arg
        and len(args) == policy.arg_count + 1
        and args[-1] is True
    )
