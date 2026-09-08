"""Recover committed workflow state whose first Celery dispatch was lost."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict

from celery import current_task
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

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
from app.services import published_image_recertification as recertification
from app.services.incident_assignment import auto_assign_owner_sync, owner_label_sync
from app.services.incident_safety import (
    REBUILD_SITE_SWEEP_KEY_PREFIX,
    build_incident_key,
)
from app.services.incident_types import IncidentFingerprint, incident_type_of
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification_sync
from app.services.post_publish_review_policy import publicly_operational_hospital_predicate
from app.services.site_build_incidents import (
    load_site_build_incident,
    open_site_build_incident,
    reopen_site_build_incident,
    touch_site_build_incident,
)
from app.services.site_revalidation_control import retry_delay
from app.workers.dispatch_auth import build_dispatch_headers, require_dispatch
from app.workers.dispatch_envelope import expected_purpose

_BATCH_SIZE: Final = 100
_REQUESTED_REDISPATCH_GRACE: Final = timedelta(minutes=2)
_QUEUED_REDISPATCH_GRACE: Final = timedelta(hours=1)
_RECERTIFY_DISPATCH_LIMIT: Final = 20
_REBUILD_SITE_ATTEMPT_BUDGET: Final = 3
_REBUILD_SITE_BUDGET_WINDOW: Final = timedelta(hours=24)
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
    recertification.RECERTIFY_OPERATION: _RedispatchPolicy(
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
        # 실행 기록을 먼저 커밋하는 경로이므로 같은 tick의 상태 변경 뒤에 둔다.
        site_builds = 0
        for hospital in hospitals:
            rebuild = _ensure_rebuild_site_run(db, hospital, observed_at)
            if rebuild is not None and _redispatch_operation_run(db, rebuild, observed_at):
                site_builds += 1
        recertifications = _dispatch_published_image_recertifications(db, observed_at)
        db.commit()
    return {
        "site_builds": site_builds,
        "site_revalidations": len(runs),
        "operation_runs": operation_redispatches,
        "image_recertifications": recertifications,
    }


def _cleared_certificate_candidates(db) -> list[ContentItem]:
    """공개 중인데 이미지 인증이 지워진 글. 제목 편집이 남기는 상태만 SQL로 값싸게 고른다.

    정책 버전 교체는 manifest로 운전하는 별도 작업이므로 여기서 다루지 않는다 — 그 조건을
    넣으면 롤아웃 때 전체 공개 글이 한꺼번에 이 sweep으로 쏟아진다. hash 불일치도 저장된
    바이트를 다시 읽어야 알 수 있어 대상이 아니다. 사람의 결정을 기다리고 **그 사고가
    아직 보이는** 판은 SQL에서 빼, 아직 자동으로 고칠 수 있는 글의 자리를 뺏지 않게 한다.
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
                ContentItem.image_policy_verified_at.is_(None),
                ~recertification.blocked_row_predicate(),
            )
            .order_by(ContentItem.published_at, ContentItem.id)
            .limit(_BATCH_SIZE)
        )
        .scalars()
        .all()
    )


def _recertify_runs_by_item(db, candidates: list[ContentItem]) -> dict[str, list[OperationRun]]:
    """후보 글들의 재인증 실행 이력을 색인된 컬럼으로 한 번에 읽는다.

    payload 안의 대상·판 비교는 Python에서 한다 — JSON 표현식으로 거르면 색인을 못 쓴다.
    """
    if not candidates:
        return {}
    hospital_ids = {item.hospital_id for item in candidates}
    wanted = {str(item.id) for item in candidates}
    grouped: dict[str, list[OperationRun]] = {}
    for run in (
        db.execute(
            select(OperationRun).where(
                OperationRun.hospital_id.in_(hospital_ids),
                OperationRun.operation_type == recertification.RECERTIFY_OPERATION,
            )
        )
        .scalars()
        .all()
    ):
        source_id = recertification.payload_source_id(run)
        if source_id in wanted:
            grouped.setdefault(source_id, []).append(run)
    return grouped


def _visible_block_incidents(db, candidates: list[ContentItem]) -> set[str]:
    """차단이 아직 사람에게 남아 있는 (글, subject)의 사고 키.

    사고가 닫혔는데 실행 이력에는 보류 코드가 남은 subject는 아무도 보지 않는 보류다.
    그 한 건만 무료 실행으로 되살린다.
    """
    if not candidates:
        return set()
    keys = {
        recertification.incident_dedupe_key(
            item.id, recertification.subject_hash_of(item)
        )
        for item in candidates
    }
    return set(
        db.execute(
            select(Incident.dedupe_key).where(
                Incident.dedupe_key.in_(keys),
                Incident.state.in_(recertification.VISIBLE_INCIDENT_STATES),
            )
        )
        .scalars()
        .all()
    )


def _dispatch_published_image_recertifications(db, observed_at: datetime) -> int:
    """인증이 지워진 공개 글의 재인증을 예산 안에서 다시 실행한다 (H-01).

    태스크는 실행 하나당 공급자를 많아야 한 번 부르므로 재실행은 이 sweep만 만든다.
    쿨다운·진행 중 판정·예산은 모두 `published_image_recertification`의 한 규칙이라,
    PATCH·sweep·운영자 재시도가 겹쳐도 (글, subject)당 유료 호출이 늘지 않는다.
    """
    candidates = _cleared_certificate_candidates(db)
    runs_by_item = _recertify_runs_by_item(db, candidates)
    visible_blocks = _visible_block_incidents(db, candidates)
    dispatched = 0
    for item in candidates:
        if dispatched >= _RECERTIFY_DISPATCH_LIMIT:
            break
        subject = recertification.subject_hash_of(item)
        runs = runs_by_item.get(str(item.id), [])
        if not recertification.sweep_may_dispatch(
            runs,
            subject,
            now=observed_at,
            block_visible=recertification.incident_dedupe_key(item.id, subject)
            in visible_blocks,
        ):
            continue
        if _start_recertify_run(db, item, subject, runs, observed_at):
            dispatched += 1
    return dispatched


def _start_recertify_run(
    db,
    item: ContentItem,
    subject_hash: str,
    runs: list[OperationRun],
    observed_at: datetime,
) -> bool:
    """REQUESTED run을 먼저 커밋하고 publish한다.

    worker가 publish를 먼저 집어도 claim할 행이 있어야 한다. publish가 실패해도 남은
    REQUESTED run을 이 파일의 재배달 sweep이 잇는다. 두 reconcile이 겹쳐 같은 키를
    만들면 뒤에 온 쪽은 건너뛴다 — 같은 subject를 두 번 사지 않기 위해서다.
    """
    target_id = str(item.id)
    task_id = str(uuid.uuid4())
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=item.hospital_id,
        operation_type=recertification.RECERTIFY_OPERATION,
        state=OperationRunState.REQUESTED,
        idempotency_key=recertification.next_sweep_key(item.id, subject_hash, runs),
        requested_by_id=None,
        task_id=task_id,
        requested_at=observed_at,
        attempt_count=0,
        total_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=recertification.request_payload(
            item.id,
            subject_hash=subject_hash,
            title=item.title,
            revision=int(getattr(item, "content_revision", 1) or 1),
        ),
        version=1,
    )
    savepoint = db.begin_nested()
    try:
        db.add(run)
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return False
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
    return True


def _ensure_rebuild_site_run(
    db, hospital: Hospital, observed_at: datetime
) -> OperationRun | None:
    """사이트 준비 재실행을 REBUILD_SITE OperationRun 예산 아래 둔다 (H-13).

    예전에는 이 sweep이 같은 병원에 `build_aeo_site`를 매분 새로 큐잉했다. claim도 시도
    기록도 없어 실패하는 병원 하나가 무한히 재실행됐다. 이제 진행 중인 run이 grace 안이면
    건너뛰고, 24시간 안에 실패가 예산만큼 쌓이면 병원 하나당 사고 한 건으로 사람에게
    넘긴다. 그 밖에는 REQUESTED run을 만들어 호출자가 서명된 재배달로 보낸다.
    """

    recent = list(
        db.execute(
            select(OperationRun)
            .where(
                OperationRun.operation_type == "REBUILD_SITE",
                OperationRun.hospital_id == hospital.id,
                OperationRun.requested_at >= observed_at - _REBUILD_SITE_BUDGET_WINDOW,
            )
            .order_by(OperationRun.requested_at.desc())
        )
        .scalars()
        .all()
    )
    for run in recent:
        if run.state in (
            OperationRunState.REQUESTED,
            OperationRunState.QUEUED,
            OperationRunState.RUNNING,
        ):
            # 진행 중인 실행이 있으면 새 run을 만들지 않는다. 유실이 의심될 때만
            # 같은 run을 재배달한다 — 판단은 일반 재배달 규칙과 하나로 유지한다.
            return run if _operation_redispatch_is_due(run, observed_at) else None
    failed = _failures_since_last_success(recent)
    if len(failed) >= _REBUILD_SITE_ATTEMPT_BUDGET:
        _open_rebuild_site_incident(
            db, hospital, max(failed, key=_run_observed_at), observed_at
        )
        return None

    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="REBUILD_SITE",
        state=OperationRunState.REQUESTED,
        idempotency_key=(
            f"{REBUILD_SITE_SWEEP_KEY_PREFIX}{hospital.id}"
            f":{observed_at.astimezone(UTC).date().isoformat()}:{_runs_started_today(recent, observed_at)}"
        ),
        requested_by_id=None,
        task_id=str(uuid.uuid4()),
        requested_at=observed_at,
        attempt_count=len(failed),
        total_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=operation_run_payloads.build_request_payload(
            operation_run_payloads.DispatchPayload(
                "hospital", str(hospital.id), "default", (str(hospital.id),)
            )
        ),
        version=1,
    )
    # 실행 기록을 먼저 커밋한다. worker가 publish를 먼저 집어도 claim할 행이 있어야 하고,
    # 같은 키를 동시에 만든 두 번째 sweep은 여기서 조용히 물러난다.
    savepoint = db.begin_nested()
    try:
        db.add(run)
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return None
    db.commit()
    return run


def _runs_started_today(recent: list[OperationRun], observed_at: datetime) -> int:
    """오늘 만들어진 사이트 준비 실행의 수 — 새 실행의 idempotency key 꼬리표다 (H-13).

    꼬리표는 날짜 안에서 절대 되돌아가면 안 된다. 실패 수로 세면 성공 한 번이 계수를 0으로
    되돌려 같은 날 이미 쓴 키가 다시 나오고, 유일 제약이 savepoint 안에서 조용히 삼켜져
    그 병원은 자정까지 자동 재실행을 못 받는다. 24시간 창 전체를 세도 어제 같은 시각의
    실행이 창 밖으로 빠지는 만큼 계수가 줄어 같은 충돌이 하루 뒤에 다시 온다. 오늘 만든
    실행은 오늘 안에는 창에서 빠지지 않으므로, 이 수는 날짜 안에서 늘기만 한다.
    """

    # 키의 날짜 부분과 같은 UTC 날짜로 비교한다 — DB 세션 시간대에 기대지 않는다.
    today = observed_at.astimezone(UTC).date()
    return sum(
        1 for run in recent if run.requested_at.astimezone(UTC).date() == today
    )


def _run_observed_at(run: OperationRun) -> datetime:
    """이 실행의 종료가 관측된 시각. 종료 기록이 없으면 마지막 상태 변경 시각을 쓴다."""

    return run.completed_at or run.updated_at or run.requested_at


def _failures_since_last_success(recent: list[OperationRun]) -> list[OperationRun]:
    """예산을 쓴 실패만 남긴다 — 성공보다 앞선 실패는 이미 해결된 일이다.

    운영자 재시도 한 번이 성공하면 그 전의 실패는 더 이상 막을 이유가 아니다. 그대로 세면
    성공한 병원이 24시간 동안 자동 재실행을 못 받고, 성공 전의 실패로 사고가 다시 열린다.
    """

    latest_success = max(
        (
            _run_observed_at(run)
            for run in recent
            if run.state == OperationRunState.SUCCEEDED
        ),
        default=None,
    )
    return [
        run
        for run in recent
        if run.state == OperationRunState.FAILED
        and (latest_success is None or _run_observed_at(run) > latest_success)
    ]


def _open_rebuild_site_incident(
    db, hospital: Hospital, newest_failed_run: OperationRun, observed_at: datetime
) -> None:
    """예산을 다 쓴 사이트 준비를 병원 하나당 사고 한 건으로 넘긴다 (H-13).

    최종 차단은 원인별로 한 건이지만, 그 한 건이 영원히 같은 값으로 굳어서도 안 된다.
    이미 열려 있으면 사고가 가리키는 실행과 다른 실행이 새로 실패했을 때만 관측을 갱신한다 —
    tick마다 세면 occurrence_count는 하루 1,400이 되어 재발 횟수라는 뜻을 잃는다. 반대로
    복구·확인으로 닫힌 건은 새 에피소드로 다시 연다. 같은 문제가 다시 예산을 다 썼는데
    조용하면 아무도 그 사실을 모른다.
    """

    incident = load_site_build_incident(db, hospital.id)
    if incident is None:
        created = open_site_build_incident(
            db,
            hospital_id=hospital.id,
            hospital_name=hospital.name,
            failed_run_id=newest_failed_run.id,
            safe_error_code="SITE_BUILD_RETRIES_EXHAUSTED",
            safe_error_message="사이트 준비 자동 재실행이 하루치 예산을 모두 사용했습니다.",
            next_action=(
                "병원 기본 정보와 공개 준비 오류를 확인하고 운영센터에서 다시 시도하세요."
            ),
            observed_at=observed_at,
        )
        if created is not None:
            return
        # 같은 tick의 다른 replica가 먼저 만들었다 — 병원 잠금은 앞선 commit에서 이미
        # 풀려 두 sweep이 여기까지 온다. IntegrityError를 그대로 올리면 뒤따르는 병원과
        # 재인증이 통째로 밀리므로, 상대가 만든 행을 다시 읽어 아래 규칙으로 이어간다.
        incident = load_site_build_incident(db, hospital.id)
        if incident is None:
            return
    if incident.state in (
        IncidentState.RECOVERED.value,
        IncidentState.ACKNOWLEDGED.value,
    ):
        closed_at = incident.recovered_at or incident.acknowledged_at
        if closed_at is not None and _run_observed_at(newest_failed_run) <= closed_at:
            # 닫히기 전의 실패다. 그 뒤로 새로 실패한 적이 없는데 다시 열면, 이미 해결된
            # 일로 새 에피소드와 Slack이 나가고 자동 재실행까지 24시간 막힌다.
            return
        reopen_site_build_incident(
            db,
            incident,
            hospital_name=hospital.name,
            failed_run_id=newest_failed_run.id,
            observed_at=observed_at,
        )
        return
    if newest_failed_run.id == incident.operation_run_id:
        # 이미 센 실패다 — 사고가 그 실행을 가리키고 있다. 관측 시각으로 가르면, 운영자
        # 재시도의 실패를 `record_task_failure`가 먼저 실은 뒤 `completed_at`이 찍히는
        # 순서 때문에 다음 tick이 같은 실패를 한 번 더 세어 재발 횟수를 부풀린다.
        return
    touch_site_build_incident(
        db, incident, failed_run_id=newest_failed_run.id, observed_at=observed_at
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
    # 자동 복구가 멈춘 이 예외도 서비스·worker 경로와 같은 규칙으로 담당자를 정한다
    # (H-15). 여기만 배정을 건너뛰면 안전하지 않은 재실행은 언제나 주인이 없다.
    auto_assign_owner_sync(db, incident, observed_at=observed_at)
    db.add(incident)
    hospital = db.get(Hospital, run.hospital_id) if run.hospital_id is not None else None
    projection = IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name=hospital.name if hospital is not None else "병원 작업",
        severity=incident.severity,
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        # 자동 배정된 담당자를 그대로 싣는다 — 주인이 정해진 예외를 Slack이 "미지정"으로
        # 알리면 아무도 자기 일로 보지 않는다.
        owner_label=owner_label_sync(db, incident.owner_id),
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
