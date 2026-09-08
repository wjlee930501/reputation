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
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    OperationRun,
    OperationRunState,
)
from app.services import operation_run_payloads
from app.services.image_engine import IMAGE_POLICY_VERSION
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint, incident_type_of
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification_sync
from app.services.post_publish_review_policy import publicly_operational_hospital_predicate
from app.services.site_revalidation_control import retry_delay
from app.workers.dispatch_auth import build_dispatch_headers, require_dispatch
from app.workers.dispatch_envelope import expected_purpose
from app.workers.generation_retry_policy import (
    PUBLISHED_RECERTIFY_ATTEMPT_BUDGET,
    GenerationRetryClass,
    published_recertify_key,
    published_recertify_sweep_key,
    retry_class_for,
)

_BATCH_SIZE: Final = 100
_REQUESTED_REDISPATCH_GRACE: Final = timedelta(minutes=2)
_QUEUED_REDISPATCH_GRACE: Final = timedelta(hours=1)
_RECERTIFY_DISPATCH_LIMIT: Final = 20
_INTEGER_ARG: Final = object()
_RECERTIFY_OPERATION: Final = "RECERTIFY_PUBLISHED_IMAGE"
_NON_TERMINAL_RUN_STATES: Final = (
    OperationRunState.REQUESTED,
    OperationRunState.QUEUED,
    OperationRunState.RUNNING,
)


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
    "RECERTIFY_PUBLISHED_IMAGE": _RedispatchPolicy(
        "app.workers.tasks.recertify_published_content_image", "content", "content_item"
    ),
}


class RecoveryCounts(TypedDict):
    site_builds: int
    site_revalidations: int
    operation_runs: int
    image_recertifications: int


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
    elif (
        run.operation_type == "TRIGGER_V0_REPORT"
        and run.state == OperationRunState.RUNNING
        and run.lease_expires_at is not None
    ):
        # V0's stage checkpoints make takeover safe after the former worker's
        # durable lease proves it can no longer own this execution.
        return run.lease_expires_at <= observed_at
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
                        and_(
                            OperationRun.operation_type == "TRIGGER_V0_REPORT",
                            OperationRun.state == OperationRunState.RUNNING,
                            OperationRun.lease_expires_at.isnot(None),
                            OperationRun.lease_expires_at <= observed_at,
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
                queue="control",
                priority=0,
                headers=build_dispatch_headers("retry-site-revalidation", str(run.id)),
            )
            run.heartbeat_at = observed_at
        operation_redispatches = 0
        for run in operation_runs:
            redispatched = _redispatch_operation_run(db, run, observed_at)
            if redispatched:
                operation_redispatches += 1
        recertifications = _dispatch_published_image_recertifications(db, observed_at)
        db.commit()
    return {
        "site_builds": len(hospitals),
        "site_revalidations": len(runs),
        "operation_runs": operation_redispatches,
        "image_recertifications": recertifications,
    }


def _cleared_certificate_candidates(db) -> list[ContentItem]:
    """공개 중인데 이미지 인증이 지워진 글. 제목 편집이 남기는 상태만 SQL로 값싸게 고른다.

    hash 불일치는 저장된 바이트를 다시 읽어야 알 수 있어 이 sweep의 대상이 아니다.
    """
    return list(
        db.execute(
            select(ContentItem)
            .join(Hospital, Hospital.id == ContentItem.hospital_id)
            .where(
                publicly_operational_hospital_predicate(),
                ContentItem.status == ContentStatus.PUBLISHED,
                ContentItem.image_url.isnot(None),
                ContentItem.image_url != "",
                or_(
                    ContentItem.image_policy_verified_at.is_(None),
                    ContentItem.image_policy_version.is_distinct_from(IMAGE_POLICY_VERSION),
                ),
            )
            .order_by(ContentItem.published_at, ContentItem.id)
            .limit(_BATCH_SIZE)
        )
        .scalars()
        .all()
    )


def _recertify_runs_by_item(db, item_ids: list[str]) -> dict[str, list[OperationRun]]:
    if not item_ids:
        return {}
    grouped: dict[str, list[OperationRun]] = {}
    for run in (
        db.execute(
            select(OperationRun).where(
                OperationRun.operation_type == _RECERTIFY_OPERATION,
                OperationRun.request_payload["source_id"].as_string().in_(item_ids),
            )
        )
        .scalars()
        .all()
    ):
        payload = _mapping(getattr(run, "request_payload", None))
        grouped.setdefault(str(payload.get("source_id")), []).append(run)
    return grouped


def _numbered_recertify_attempts(
    runs: list[OperationRun], base: str
) -> list[tuple[int, OperationRun]]:
    """이 (글, 판)의 시도를 실행 키에 박힌 번호로 정렬한다. 시각 없이도 순서가 정해진다."""
    numbered: list[tuple[int, OperationRun]] = []
    for run in runs:
        key = str(getattr(run, "idempotency_key", "") or "")
        suffix = key[len(base) :]
        if key == base:
            numbered.append((1, run))
        elif key.startswith(base) and suffix.startswith(":s") and suffix[2:].isdigit():
            numbered.append((int(suffix[2:]), run))
    return sorted(numbered, key=lambda pair: pair[0])


def _recertify_sweep_key(item: ContentItem, runs: list[OperationRun]) -> str | None:
    """자동 재실행이 아직 이어져야 하는 글에만 새 실행 키를 준다."""
    if any(run.state in _NON_TERMINAL_RUN_STATES for run in runs):
        # PATCH 디스패치든 이전 sweep이든 진행 중인 실행이 있으면 겹쳐 사지 않는다.
        return None
    revision = int(getattr(item, "content_revision", 1) or 1)
    base = published_recertify_key(item.id, revision)
    numbered = _numbered_recertify_attempts(runs, base)
    failed = [pair for pair in numbered if pair[1].state == OperationRunState.FAILED]
    if failed and retry_class_for(str(failed[-1][1].safe_error_code or "")) is not (
        GenerationRetryClass.ENVIRONMENT_RECOVERABLE
    ):
        # 거절·이미지 없음은 사람의 결정이다. 다시 사도 같은 답이 나온다.
        return None
    if len(failed) >= PUBLISHED_RECERTIFY_ATTEMPT_BUDGET:
        # 예산 소진. 마지막 실행이 이미 incident를 열었다.
        return None
    attempt = numbered[-1][0] + 1 if numbered else 1
    return published_recertify_sweep_key(item.id, revision, attempt)


def _dispatch_published_image_recertifications(db, observed_at: datetime) -> int:
    """인증이 지워진 공개 글의 재인증을 예산 안에서 다시 실행한다 (H-01).

    일시 오류로 끝난 재인증은 태스크의 자기 재시도만으로는 되살아나지 않는다. 이 sweep이
    backstop이라 공개가 내려간 글이 사람을 기다리며 방치되지 않는다.
    """
    candidates = _cleared_certificate_candidates(db)
    runs_by_item = _recertify_runs_by_item(db, [str(item.id) for item in candidates])
    dispatched = 0
    for item in candidates:
        if dispatched >= _RECERTIFY_DISPATCH_LIMIT:
            break
        key = _recertify_sweep_key(item, runs_by_item.get(str(item.id), []))
        if key is None:
            continue
        _start_recertify_run(db, item, key, observed_at)
        dispatched += 1
    return dispatched


def _start_recertify_run(db, item: ContentItem, key: str, observed_at: datetime) -> None:
    """REQUESTED run을 먼저 커밋하고 publish한다.

    worker가 publish를 먼저 집어도 claim할 행이 있어야 한다. publish가 실패해도 남은
    REQUESTED run을 이 파일의 재배달 sweep이 잇는다.
    """
    target_id = str(item.id)
    task_id = str(uuid.uuid4())
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=item.hospital_id,
        operation_type=_RECERTIFY_OPERATION,
        state=OperationRunState.REQUESTED,
        idempotency_key=key,
        requested_by_id=None,
        task_id=task_id,
        requested_at=observed_at,
        attempt_count=0,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=operation_run_payloads.build_request_payload(
            operation_run_payloads.DispatchPayload(
                "content_item", target_id, "content", (target_id,)
            )
        ),
        version=1,
    )
    db.add(run)
    db.commit()
    celery_app.send_task(
        "app.workers.tasks.recertify_published_content_image",
        args=[target_id],
        queue="content",
        headers={
            **build_dispatch_headers("recertify-published-image", target_id),
            "operation_run_id": str(run.id),
        },
        task_id=task_id,
    )


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
    run.heartbeat_at = None
    run.lease_owner = None
    run.lease_expires_at = None
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
