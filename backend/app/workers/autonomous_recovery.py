"""Recover committed workflow state whose first Celery dispatch was lost."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict

from celery import current_task
from sqlalchemy import and_, func, or_, select

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    OperationRun,
    OperationRunState,
)
from app.services import operation_run_payloads
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint
from app.services.site_revalidation_control import retry_delay
from app.workers.dispatch_auth import build_dispatch_headers, require_dispatch
from app.workers.dispatch_envelope import expected_purpose

_BATCH_SIZE: Final = 100
_REQUESTED_REDISPATCH_GRACE: Final = timedelta(minutes=2)
_QUEUED_REDISPATCH_GRACE: Final = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class _RedispatchPolicy:
    task_name: str
    queue: str
    target_type: str
    arg_count: int
    allow_rebuild_true_arg: bool = False


_OPERATION_REDISPATCH_POLICIES: Final[dict[str, _RedispatchPolicy]] = {
    "TRIGGER_V0_REPORT": _RedispatchPolicy(
        "app.workers.tasks.trigger_v0_report", "reports", "hospital", 1
    ),
    "RUN_SOV": _RedispatchPolicy("app.workers.tasks.run_sov_for_hospital", "sov", "hospital", 1),
    "REBUILD_SITE": _RedispatchPolicy("app.workers.tasks.build_aeo_site", "default", "hospital", 1),
    "GENERATE_MONTHLY_REPORT": _RedispatchPolicy(
        "app.workers.tasks.generate_monthly_report_for_hospital",
        "reports",
        "hospital",
        3,
        True,
    ),
    "REGENERATE_CONTENT": _RedispatchPolicy(
        "app.workers.tasks.regenerate_content_item", "content", "content_item", 1
    ),
    "REGENERATE_CONTENT_IMAGE": _RedispatchPolicy(
        "app.workers.tasks.generate_content_image", "content", "content_item", 1
    ),
}


class RecoveryCounts(TypedDict):
    site_builds: int
    site_revalidations: int
    operation_runs: int


def _now() -> datetime:
    return datetime.now(UTC)


def _revalidation_is_due(run: OperationRun, observed_at: datetime) -> bool:
    delay = retry_delay(run.attempt_count)
    if delay is None:
        return False
    last_attempt = run.heartbeat_at or run.started_at or run.requested_at
    return last_attempt <= observed_at - timedelta(seconds=delay)


def _operation_redispatch_is_due(run: OperationRun, observed_at: datetime) -> bool:
    """Distinguish a publish that may be lost from legitimate broker queue time."""

    if run.state == OperationRunState.REQUESTED:
        last_transition = run.requested_at
        grace = _REQUESTED_REDISPATCH_GRACE
    elif run.state == OperationRunState.QUEUED:
        last_transition = run.queued_at or run.requested_at
        grace = _QUEUED_REDISPATCH_GRACE
    else:
        return False
    return last_transition <= observed_at - grace


@celery_app.task(name="app.workers.autonomous_recovery.reconcile")
def reconcile() -> RecoveryCounts:
    """Re-dispatch idempotent work from committed database truth."""

    require_dispatch(current_task, "reconcile-autonomous-workflows")
    observed_at = _now()
    with SyncSessionLocal() as db:
        hospitals = list(
            db.execute(
                select(Hospital)
                .where(
                    Hospital.profile_complete.is_(True),
                    Hospital.v0_report_done.is_(True),
                    Hospital.site_built.is_(False),
                )
                .order_by(Hospital.created_at, Hospital.id)
                .with_for_update(skip_locked=True)
                .limit(_BATCH_SIZE)
            )
            .scalars()
            .all()
        )
        runs = [
            run
            for run in db.execute(
                select(OperationRun)
                .where(
                    OperationRun.operation_type == "SITE_REVALIDATION",
                    OperationRun.state == OperationRunState.RUNNING,
                )
                .order_by(OperationRun.heartbeat_at, OperationRun.id)
                .with_for_update(skip_locked=True)
                .limit(_BATCH_SIZE)
            )
            .scalars()
            .all()
            if _revalidation_is_due(run, observed_at)
        ]
        operation_runs = [
            run
            for run in db.execute(
                select(OperationRun)
                .where(
                    OperationRun.operation_type.in_(tuple(_OPERATION_REDISPATCH_POLICIES)),
                    or_(
                        and_(
                            OperationRun.state == OperationRunState.REQUESTED,
                            OperationRun.requested_at
                            <= observed_at - _REQUESTED_REDISPATCH_GRACE,
                        ),
                        and_(
                            OperationRun.state == OperationRunState.QUEUED,
                            func.coalesce(OperationRun.queued_at, OperationRun.requested_at)
                            <= observed_at - _QUEUED_REDISPATCH_GRACE,
                        ),
                    ),
                )
                .order_by(OperationRun.requested_at, OperationRun.id)
                .with_for_update(skip_locked=True)
                .limit(_BATCH_SIZE)
            )
            .scalars()
            .all()
            if _operation_redispatch_is_due(run, observed_at)
        ]

        for hospital in hospitals:
            hospital_id = str(hospital.id)
            celery_app.send_task(
                "app.workers.tasks.build_aeo_site",
                args=[hospital_id],
                queue="default",
                headers=build_dispatch_headers("build-aeo-site", hospital_id),
            )
        for run in runs:
            celery_app.send_task(
                "app.workers.tasks.retry_site_revalidation",
                args=[str(run.id), run.attempt_count],
                queue="default",
                headers=build_dispatch_headers("retry-site-revalidation", str(run.id)),
            )
            run.heartbeat_at = observed_at
        operation_redispatches = 0
        for run in operation_runs:
            redispatched = _redispatch_operation_run(db, run, observed_at)
            if redispatched:
                operation_redispatches += 1
        db.commit()
    return {
        "site_builds": len(hospitals),
        "site_revalidations": len(runs),
        "operation_runs": operation_redispatches,
    }


def _redispatch_operation_run(db, run: OperationRun, observed_at: datetime) -> bool:
    policy = _OPERATION_REDISPATCH_POLICIES.get(str(run.operation_type))
    dispatch = _validated_dispatch(db, run, policy)
    if policy is None or dispatch is None or not run.task_id:
        _fail_unsafe_operation_run(db, run, observed_at)
        return False
    celery_app.send_task(
        policy.task_name,
        args=list(dispatch.task_args),
        queue=policy.queue,
        headers=_operation_run_dispatch_headers(policy, dispatch, run),
        task_id=run.task_id,
    )
    run.state = OperationRunState.QUEUED
    run.queued_at = observed_at
    run.safe_error_code = None
    run.safe_error_message = None
    run.version += 1
    return True


def _operation_run_dispatch_headers(
    policy: _RedispatchPolicy,
    dispatch: operation_run_payloads.DispatchPayload,
    run: OperationRun,
) -> dict[str, str]:
    """Rebuild the signed-dispatch seed headers for a lost OperationRun publish."""

    headers = build_dispatch_headers(expected_purpose(policy.task_name), dispatch.target_id)
    headers["operation_run_id"] = str(run.id)
    return headers


def _validated_dispatch(
    db,
    run: OperationRun,
    policy: _RedispatchPolicy | None,
) -> operation_run_payloads.DispatchPayload | None:
    if policy is None:
        return None
    try:
        dispatch = operation_run_payloads.parse_stored_dispatch(
            run.request_payload.get("_dispatch")
        )
    except operation_run_payloads.UnsafeDispatchPayload:
        return None
    if dispatch.queue != policy.queue or dispatch.target_type != policy.target_type:
        return None
    if not _args_match_policy(dispatch, policy):
        return None
    if run.hospital_id is None:
        return None
    match policy.target_type:
        case "hospital":
            return dispatch if dispatch.target_id == str(run.hospital_id) else None
        case "content_item":
            try:
                content_id = uuid.UUID(dispatch.target_id)
            except ValueError:
                return None
            item = db.get(ContentItem, content_id)
            if item is not None and item.hospital_id == run.hospital_id:
                return dispatch
            return None
    return None


def _args_match_policy(
    dispatch: operation_run_payloads.DispatchPayload,
    policy: _RedispatchPolicy,
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


def _fail_unsafe_operation_run(db, run: OperationRun, observed_at: datetime) -> None:
    run.state = OperationRunState.FAILED
    run.completed_at = observed_at
    run.safe_error_code = "UNSAFE_STORED_DISPATCH"
    run.safe_error_message = "저장된 작업 재실행 정보가 안전한 허용 목록과 맞지 않습니다."
    run.version += 1
    db.add(
        Incident(
            id=uuid.uuid4(),
            hospital_id=run.hospital_id,
            operation_run_id=run.id,
            dedupe_key=build_incident_key(
                "autonomous_recovery",
                "operation_run",
                str(run.id),
                IncidentFingerprint.VALIDATION_FAILED,
            ),
            incident_type="UNSAFE_STORED_DISPATCH",
            state=IncidentState.OPEN.value,
            severity=IncidentSeverity.HIGH.value,
            customer_impact="저장된 운영 작업을 안전하게 재실행할 수 없어 자동 복구가 중단되었습니다.",
            source_type="OPERATION_RUN",
            source_id=str(run.id),
            safe_error_code=run.safe_error_code,
            safe_error_message=run.safe_error_message,
            next_action=(
                "운영 센터에서 작업 상세를 확인한 뒤 원 요청을 다시 실행해 주세요. "
                "같은 문제가 반복되면 개발팀에 작업 ID를 전달해 주세요."
            ),
            admin_path="/operations",
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            created_at=observed_at,
            updated_at=observed_at,
        )
    )
