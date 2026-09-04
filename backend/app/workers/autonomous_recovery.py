"""Recover committed workflow state whose first Celery dispatch was lost."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict

from celery import current_task
from sqlalchemy import and_, func, or_, select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    OperationRun,
    OperationRunState,
)
from app.services import operation_run_payloads
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint, incident_type_of
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification_sync
from app.services.site_revalidation_control import retry_delay
from app.workers.dispatch_auth import build_dispatch_headers, require_dispatch
from app.workers.dispatch_envelope import expected_purpose

_BATCH_SIZE: Final = 100
_REQUESTED_REDISPATCH_GRACE: Final = timedelta(minutes=2)
_QUEUED_REDISPATCH_GRACE: Final = timedelta(hours=1)
_INTEGER_ARG: Final = object()


@dataclass(frozen=True, slots=True)
class _RedispatchPolicy:
    task_name: str
    queue: str
    target_type: str
    allowed_arg_suffixes: tuple[tuple[object, ...], ...] = ((),)


_OPERATION_REDISPATCH_POLICIES: Final[dict[str, _RedispatchPolicy]] = {
    "TRIGGER_V0_REPORT": _RedispatchPolicy(
        "app.workers.tasks.trigger_v0_report", "reports", "hospital"
    ),
    "RUN_SOV": _RedispatchPolicy(
        "app.workers.tasks.run_sov_for_hospital",
        "sov",
        "hospital",
        ((), ("monthly", _INTEGER_ARG, _INTEGER_ARG)),
    ),
    "REBUILD_SITE": _RedispatchPolicy(
        "app.workers.tasks.build_aeo_site", "default", "hospital"
    ),
    "GENERATE_MONTHLY_REPORT": _RedispatchPolicy(
        "app.workers.tasks.generate_monthly_report_for_hospital",
        "reports",
        "hospital",
        (
            (_INTEGER_ARG, _INTEGER_ARG),
            (_INTEGER_ARG, _INTEGER_ARG, True),
            (_INTEGER_ARG, _INTEGER_ARG, True, True),
        ),
    ),
    "REGENERATE_CONTENT": _RedispatchPolicy(
        "app.workers.tasks.regenerate_content_item", "content", "content_item"
    ),
    "REGENERATE_CONTENT_IMAGE": _RedispatchPolicy(
        "app.workers.tasks.generate_content_image", "content", "content_item"
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
                    or_(
                        Hospital.site_built.is_(False),
                        # 허브는 준비됐는데 기본 주소 자동 활성화가 유실된 병원. STEP5 재촉
                        # Slack을 없앤 뒤에는 이 재실행이 유일한 복구 경로다 —
                        # build_aeo_site는 이미 ACTIVE·PAUSED·자기 도메인 병원을 건드리지
                        # 않으므로 재배달해도 안전하다.
                        and_(
                            Hospital.site_built.is_(True),
                            Hospital.site_live.is_(False),
                            Hospital.status == HospitalStatus.PENDING_DOMAIN,
                            or_(Hospital.aeo_domain.is_(None), Hospital.aeo_domain == ""),
                        ),
                    ),
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
    if dispatch is None:
        dispatch = _rebuild_dispatch(db, run, policy)
    if policy is None or dispatch is None:
        _fail_unsafe_operation_run(db, run, observed_at)
        return False
    if not run.task_id:
        run.task_id = str(uuid.uuid4())
    celery_app.send_task(
        policy.task_name,
        args=list(dispatch.task_args),
        queue=policy.queue,
        headers=_operation_run_dispatch_headers(policy, dispatch, run),
        task_id=run.task_id,
    )
    run.state = OperationRunState.QUEUED
    run.queued_at = observed_at
    run.completed_at = None
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


def _rebuild_dispatch(
    db,
    run: OperationRun,
    policy: _RedispatchPolicy | None,
) -> operation_run_payloads.DispatchPayload | None:
    """Reconstruct an allowlisted dispatch from durable run facts, never stored IDs."""

    if policy is None or run.hospital_id is None:
        return None
    hospital_id = str(run.hospital_id)
    target_id = hospital_id
    if policy.target_type == "content_item":
        payload = _mapping(getattr(run, "request_payload", None))
        if payload.get("source_type") != "content_item":
            return None
        raw_target = payload.get("source_id")
        if not isinstance(raw_target, str):
            return None
        try:
            content_id = uuid.UUID(raw_target)
        except ValueError:
            return None
        item = db.get(ContentItem, content_id)
        if item is None or item.hospital_id != run.hospital_id:
            return None
        target_id = str(content_id)

    suffix: tuple[object, ...]
    if str(run.operation_type) == "GENERATE_MONTHLY_REPORT":
        period = _stored_period(run)
        if period is None:
            return None
        flags = _stored_monthly_report_flags(run)
        suffix = (*period, *flags)
    elif str(run.operation_type) == "RUN_SOV" and _stored_sov_mode(run) == "monthly":
        period = _stored_period(run)
        if period is None:
            return None
        suffix = ("monthly", *period)
    else:
        suffix = ()

    rebuilt = operation_run_payloads.DispatchPayload(
        policy.target_type,
        target_id,
        policy.queue,
        (target_id, *suffix),
    )
    return rebuilt if _args_match_policy(rebuilt, policy) else None


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _raw_stored_args(run: OperationRun) -> tuple[object, ...]:
    payload = _mapping(getattr(run, "request_payload", None))
    dispatch = _mapping(payload.get("_dispatch"))
    args = dispatch.get("task_args")
    return tuple(args) if isinstance(args, list) else ()


def _stored_period(run: OperationRun) -> tuple[int, int] | None:
    payload = _mapping(getattr(run, "request_payload", None))
    summary = _mapping(getattr(run, "result_summary", None))
    candidates: list[tuple[object, object]] = [
        (summary.get("period_year"), summary.get("period_month")),
        (payload.get("period_year"), payload.get("period_month")),
    ]
    raw_args = _raw_stored_args(run)
    if str(run.operation_type) == "RUN_SOV" and len(raw_args) >= 4:
        candidates.append((raw_args[2], raw_args[3]))
    elif len(raw_args) >= 3:
        candidates.append((raw_args[1], raw_args[2]))
    for value in (
        summary.get("measurement_month"),
        payload.get("measurement_month"),
        payload.get("source_id") if payload.get("source_type") == "MONTHLY_SCHEDULE" else None,
    ):
        if isinstance(value, str):
            try:
                year_text, month_text = value.split("-", 1)
                candidates.append((int(year_text), int(month_text)))
            except ValueError:
                pass
    for year, month in candidates:
        if (
            type(year) is int
            and 2000 <= year <= 2200
            and type(month) is int
            and 1 <= month <= 12
        ):
            return year, month
    return None


def _stored_monthly_report_flags(run: OperationRun) -> tuple[object, ...]:
    payload = _mapping(getattr(run, "request_payload", None))
    summary = _mapping(getattr(run, "result_summary", None))
    raw_args = _raw_stored_args(run)
    rebuild = any(
        value is True
        for value in (
            payload.get("rebuild"),
            summary.get("rebuild"),
            raw_args[3] if len(raw_args) >= 4 else None,
        )
    )
    automatic = any(
        value is True
        for value in (
            payload.get("automatic_recovery"),
            summary.get("automatic_recovery"),
            raw_args[4] if len(raw_args) >= 5 else None,
        )
    )
    if automatic:
        return True, True
    return (True,) if rebuild else ()


def _stored_sov_mode(run: OperationRun) -> str:
    payload = _mapping(getattr(run, "request_payload", None))
    summary = _mapping(getattr(run, "result_summary", None))
    raw_args = _raw_stored_args(run)
    if any(
        value == "monthly"
        for value in (
            summary.get("measurement_mode"),
            payload.get("measurement_mode"),
            raw_args[1] if len(raw_args) >= 2 else None,
        )
    ) or str(getattr(run, "idempotency_key", "") or "").startswith("monthly-sov:"):
        return "monthly"
    return "weekly"


def _args_match_policy(
    dispatch: operation_run_payloads.DispatchPayload,
    policy: _RedispatchPolicy,
) -> bool:
    args = dispatch.task_args
    if not args or args[0] != dispatch.target_id:
        return False
    return any(
        len(args) == len(suffix) + 1
        and all(
            _arg_matches_shape(value, expected)
            for value, expected in zip(args[1:], suffix, strict=True)
        )
        for suffix in policy.allowed_arg_suffixes
    )


def _arg_matches_shape(value: object, expected: object) -> bool:
    if expected is _INTEGER_ARG:
        return type(value) is int
    if expected is True:
        return value is True
    return value == expected


def _fail_unsafe_operation_run(db, run: OperationRun, observed_at: datetime) -> None:
    run.state = OperationRunState.FAILED
    run.completed_at = observed_at
    run.safe_error_code = "UNSAFE_STORED_DISPATCH"
    run.safe_error_message = "저장된 작업 재실행 정보가 안전한 허용 목록과 맞지 않습니다."
    run.version += 1
    incident = Incident(
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
        version=1,
        episode_seq=1,
    )
    db.add(incident)
    hospital = db.get(Hospital, run.hospital_id) if run.hospital_id is not None else None
    projection = IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name=hospital.name if hospital is not None else "병원 작업",
        severity=incident.severity,
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        owner_label="미지정",
        sla_label="확인 필요",
        hospital_id=incident.hospital_id,
        operation_run_id=incident.operation_run_id,
        version=incident.version,
        problem=incident.safe_error_message or "자동 복구가 중단되었습니다.",
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )
    enqueue_notification_sync(
        db,
        build_open_incident_notification(projection, settings.ADMIN_BASE_URL),
        now=observed_at,
    )
