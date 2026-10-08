"""Run-correlated incident/outbox projection for generic Celery outcomes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import String, and_, case, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    NotificationOutbox,
    NotificationOutboxState,
    OperationRun,
    OperationRunState,
)
from app.services.dependency_incident_helpers import open_notice_exists_sync
from app.services.incident_assignment import auto_assign_owner_sync, owner_label_sync
from app.services.incident_safety import (
    REBUILD_SITE_SWEEP_KEY_PREFIX,
    build_incident_key,
    site_build_incident_key,
)
from app.services.incident_types import IncidentFingerprint, incident_type_of
from app.services.notification_contracts import (
    IncidentSlackProjection,
    NotificationIntent,
    validate_message,
)
from app.services.notification_messages import (
    build_open_incident_notification,
    build_recovered_incident_notification,
)
from app.services.site_build_incidents import (
    load_site_build_incident,
    open_site_build_incident,
    reopen_site_build_incident,
    touch_site_build_incident,
)

_FINGERPRINT = IncidentFingerprint.UNKNOWN
_CLASSIFIED_GENERATION_OPERATIONS = {
    "REGENERATE_CONTENT",
    "REGENERATE_CONTENT_IMAGE",
    "RUN_SOV",
}
_GENERIC_FAILURE_SLACK_SUPPRESSED_OPERATIONS = {"RUN_SOV"}
# 자동 복구 sweep이 주인인 작업 (H-13). 최종 차단은 sweep이 병원 하나당
# SITE_BUILD_RETRIES_EXHAUSTED 한 건으로 넘긴다
# (`workers/autonomous_recovery._open_rebuild_site_incident`).
_SWEEP_OWNED_OPERATIONS = {"REBUILD_SITE"}


class SignalRequest(Protocol):
    headers: dict[str, str] | None


class SignalTask(Protocol):
    request: SignalRequest


def record_task_failure(task: SignalTask | None, task_id: str | None) -> bool:
    identity = _run_identity(task, task_id)
    if identity is None:
        return False
    run_id, worker_task_id = identity
    with SyncSessionLocal() as db:
        run = _tracked_run(db, run_id, worker_task_id)
        if run is None:
            return False
        if run.operation_type in _SWEEP_OWNED_OPERATIONS:
            if str(run.idempotency_key or "").startswith(REBUILD_SITE_SWEEP_KEY_PREFIX):
                # sweep이 만든 자동 시도다. 시도 하나하나를 사람의 할 일로 만들지 않는다.
                # 예산을 다 쓰기 전의 실패마다 generic 사고와 Slack을 열면 하루 세 번
                # 조치 요청이 생기고, 뒤이은 자동 성공은 그중 자기 run의 사고 한 건만
                # 닫는다. 예산을 다 쓴 뒤의 최종 차단은 sweep이 병원 하나당 한 건으로
                # 넘기고, run 자체는 이 반환값과 무관하게
                # `workers/operation_run_signals`가 FAILED로 종결한다.
                return False
            if _record_site_build_operator_failure(db, run):
                db.commit()
                return True
            # 병원이 없는 REBUILD_SITE 실행에만 남는 길이다 — 병원 단위 사고 키가 없어
            # 실을 곳이 없으므로, 이 시도 하나를 아래 generic 경로가 사람에게 보인다.
        if (
            run.operation_type in _CLASSIFIED_GENERATION_OPERATIONS
            and run.safe_error_code
            and run.safe_error_code != "TASK_FAILED"
        ):
            # The task body has already terminalized the exact run and projected a
            # pipeline-specific incident.  The Celery failure signal carries no
            # new truth and must not create a second generic Slack episode.
            return False
        previous_state = db.scalar(
            select(Incident.state).where(Incident.dedupe_key == _incident_key(run.id))
        )
        incident = _open_incident(db, run)
        # 이 경로로 열린 예외도 서비스 경로와 같은 규칙으로 담당자를 정한다 (H-15).
        # 여기만 배정을 건너뛰면 generic Celery 실패는 언제나 주인이 없다.
        assigned_owner_id = auto_assign_owner_sync(
            db, incident, observed_at=incident.first_seen_at
        )
        if assigned_owner_id is not None:
            _audit(
                db,
                incident,
                "incident_assigned",
                detail_extra={
                    "auto_assigned": True,
                    "auto_assigned_to": str(assigned_owner_id),
                },
            )
        should_notify = run.operation_type not in _GENERIC_FAILURE_SLACK_SUPPRESSED_OPERATIONS
        if should_notify and (
            previous_state is None
            or previous_state
            in {
                IncidentState.RECOVERED.value,
                IncidentState.ACKNOWLEDGED.value,
            }
        ):
            _enqueue(
                db,
                build_open_incident_notification(
                    _projection(db, incident), settings.ADMIN_BASE_URL
                ),
            )
        _audit(db, incident, "generic_task_failure_opened")
        db.commit()
    return True


def record_task_success(task: SignalTask | None, task_id: str | None) -> bool:
    identity = _run_identity(task, task_id)
    if identity is None:
        return False
    run_id, worker_task_id = identity
    with SyncSessionLocal() as db:
        run = _tracked_run(db, run_id, worker_task_id)
        if run is None:
            return False
        if _deferred(run):
            # 태스크는 정상 반환했지만 일을 끝낸 것이 아니라 미뤘다(예: V0 비용 창). 결과물이 없는데
            # 복구로 닫으면 사람이 봐야 할 사고가 조용히 사라진다.
            return False
        incident = _recoverable_incident(db, run)
        if incident is None:
            return False
        if incident.state == IncidentState.OPEN.value:
            retrying = _transition_incident(
                db,
                incident,
                expected_state=IncidentState.OPEN,
                next_state=IncidentState.RETRYING,
            )
            if retrying is None:
                return False
            incident = retrying
            _audit(db, incident, "incident_retrying")
        recovered = _transition_incident(
            db,
            incident,
            expected_state=IncidentState.RETRYING,
            next_state=IncidentState.RECOVERED,
            recovered=True,
        )
        if recovered is None:
            return False
        incident = recovered
        # BACKGROUND_TASK_FAILED recovers when the next scheduled attempt succeeds.
        # Nobody started that work, so the recovery is informational and the system
        # closes the incident itself instead of queueing a "확인 완료" click. It is
        # still sent whenever the "운영 확인 필요" notice actually reached the outbox:
        # a delivered OPEN must always be closed by its RECOVERED. When no OPEN was
        # queued — a run the pipeline already terminalized with its own classified
        # incident, so `record_task_failure` returned early — nothing is owed and the
        # DB incident plus its audit trail stay the only record.
        if open_notice_exists_sync(db, incident.id):
            _enqueue(
                db,
                build_recovered_incident_notification(
                    _projection(db, incident), settings.ADMIN_BASE_URL
                ),
            )
        _audit(db, incident, "incident_recovered")
        acknowledged = _transition_incident(
            db,
            incident,
            expected_state=IncidentState.RECOVERED,
            next_state=IncidentState.ACKNOWLEDGED,
            acknowledged=True,
        )
        if acknowledged is not None:
            incident = acknowledged
            _audit(db, incident, "incident_auto_acknowledged")
        db.commit()
    return True


def _record_site_build_operator_failure(db: Session, run: OperationRun) -> bool:
    """사람이 시작한 사이트 준비 재시도의 실패를 이 병원의 사고 한 건에 싣는다 (H-13).

    운영센터 재시도는 sweep이 더 이상 고르지 않는 병원(이미 ACTIVE·site_built 등)에서도
    눌린다. 그 실패를 sweep에게 미루면 아무도 알리지 않아, 누른 사람은 실패한 줄 모른다.
    그렇다고 실행 단위 generic 사고를 열면 같은 원인이 두 줄이 된다 — 그 실행은 sweep의
    예산에도 함께 세어져, 뒤이은 자동 실패 두 번이 병원 단위 최종 차단을 두 번째 OPEN과
    두 번째 Slack으로 열고, 재시도의 성공은 그중 한 건만 닫는다. 그래서 열려 있으면 실어
    주고, 닫혀 있으면 새 에피소드로 되돌리고, 아직 없으면 같은 dedupe 키로 이 병원의
    사고를 여기서 연다. 그 뒤에 예산이 다 차도 sweep은 이미 있는 한 건을 만질 뿐이다.
    """

    if run.hospital_id is None:
        return False
    observed_at = datetime.now(UTC)
    incident = load_site_build_incident(db, run.hospital_id)
    if incident is None:
        opened = open_site_build_incident(
            db,
            hospital_id=run.hospital_id,
            hospital_name=_hospital_name(db, run.hospital_id),
            failed_run_id=run.id,
            safe_error_code="SITE_BUILD_FAILED",
            safe_error_message="병원 공개 페이지 준비 작업이 실패했습니다.",
            next_action=(
                "운영 관제에서 실패한 작업의 원인을 확인해 해결한 뒤 다시 시도하세요."
            ),
            observed_at=observed_at,
        )
        if opened is not None:
            return True
        # 같은 순간의 sweep이 먼저 만들었다. 상대가 만든 한 건에 이 실패를 싣는다.
        incident = load_site_build_incident(db, run.hospital_id)
        if incident is None:
            return False
    if incident.state in (IncidentState.OPEN.value, IncidentState.RETRYING.value):
        touch_site_build_incident(
            db, incident, failed_run_id=run.id, observed_at=observed_at
        )
        return True
    reopen_site_build_incident(
        db,
        incident,
        hospital_name=_hospital_name(db, run.hospital_id),
        failed_run_id=run.id,
        observed_at=observed_at,
    )
    return True


def _hospital_name(db: Session, hospital_id: uuid.UUID) -> str:
    return db.scalar(select(Hospital.name).where(Hospital.id == hospital_id)) or "병원 작업"


def _run_identity(
    task: SignalTask | None, task_id: str | None
) -> tuple[uuid.UUID, str] | None:
    if task is None or task_id is None or not task_id.strip():
        return None
    headers = getattr(task.request, "headers", None)
    if not isinstance(headers, dict):
        return None
    raw = headers.get("operation_run_id") or headers.get(
        "reputation_dispatch_operation_run_id"
    )
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw), task_id.strip()
    except ValueError:
        return None


def _deferred(run: OperationRun) -> bool:
    state = getattr(run.state, "value", run.state)
    return state == OperationRunState.QUEUED.value and run.not_before_at is not None


def _tracked_run(db: Session, run_id: uuid.UUID, task_id: str) -> OperationRun | None:
    return db.scalar(
        select(OperationRun).where(OperationRun.id == run_id, OperationRun.task_id == task_id)
    )


# 더 나중 실행이 옛 사고를 대신하는 종결. SUCCEEDED는 일을 끝냈고, FAILED는 자기 사고로 지금
# 상태를 말한다. PARTIAL·CANCELLED·건너뛰기(OPERATION_SKIPPED)는 자기 사고를 열지 않으므로
# 옛 사고를 닫으면 남은 문제가 아무 데도 보이지 않는다.
_SUPERSEDING_RUN_STATES = (
    OperationRunState.SUCCEEDED.value,
    OperationRunState.FAILED.value,
)

# 한 tick에 닫는 해결된 사고 상한. 밀린 백로그도 몇 분 안에 비워진다.
RESOLVED_TASK_INCIDENT_BATCH = 50


def _dispatch_target(payload_column):
    return payload_column["_dispatch"]["target_id"].as_string()


def _resolved_run_condition():
    """이 실행이 맡았던 일이 이미 끝났다는 DB 근거.

    사고는 실행(run) 하나에 묶여 그 실행의 성공만 닫았다(`record_task_success`). 다음 주
    측정·다음 생성처럼 **새 실행**이 같은 일을 끝내도 옛 사고는 계속 열려 일일 요약의
    '백그라운드 작업 중단'으로 쌓였다(2026-10-02 운영 96건 중 86건). 셋 중 하나면 해결이다.
    - 그 실행 자체가 결국 SUCCEEDED로 끝났다(실패 신호 뒤 같은 run의 재시도가 성공).
    - 같은 종류·같은 대상의 더 나중 실행이 SUCCEEDED거나 FAILED다. 그 실행이 이 일을 이어받았고,
      실패였다면 그 실행의 사고가 지금 상태를 대신 말한다(PARTIAL·CANCELLED는 대신하지 않는다).
    - 대상이 콘텐츠이고 그 글이 실행 뒤에 처음 공개됐다.

    대상은 `_dispatch.target_id`다. 저장된 배포 정보가 없는 실행(대상 NULL)은 NULL=NULL이
    참이 아니라 영영 짝을 찾지 못했다 — 그때는 같은 병원(병원 없음끼리 포함)을 같은 대상으로 본다.
    """

    later = OperationRun.__table__.alias("later_run")
    target = _dispatch_target(OperationRun.request_payload)
    later_target = _dispatch_target(later.c.request_payload)
    same_target = or_(
        later_target == target,
        and_(
            target.is_(None),
            later_target.is_(None),
            later.c.hospital_id.is_not_distinct_from(OperationRun.hospital_id),
        ),
    )
    later_finished = exists().where(
        later.c.operation_type == OperationRun.operation_type,
        same_target,
        later.c.state.in_(_SUPERSEDING_RUN_STATES),
        later.c.requested_at > OperationRun.requested_at,
    )
    published_after = and_(
        OperationRun.request_payload["_dispatch"]["target_type"].as_string() == "content_item",
        exists().where(
            ContentItem.id.cast(String) == target,
            ContentItem.first_published_at > OperationRun.requested_at,
        ),
    )
    return or_(
        OperationRun.state == OperationRunState.SUCCEEDED.value,
        later_finished,
        published_after,
    )


def close_resolved_task_incidents(
    db: Session, *, limit: int = RESOLVED_TASK_INCIDENT_BATCH
) -> int:
    """이미 해결된 일을 가리키는 generic 작업 실패 사고를 조용히 닫는다.

    Slack 복구 알림은 보내지 않는다 — 지나간 일을 한꺼번에 정리하는 것이라 채널에 복구
    메시지가 쏟아진다. 감사 기록만 남기고, 사람에게 보이는 표면(운영센터·일일 요약)에서
    빠지게 한다. 커밋은 호출자가 한다.
    """

    live = (IncidentState.OPEN.value, IncidentState.RETRYING.value)
    candidates = list(
        db.execute(
            select(Incident)
            .join(OperationRun, OperationRun.id == Incident.operation_run_id)
            .where(
                Incident.incident_type == "BACKGROUND_TASK_FAILED",
                Incident.source_type == "OPERATION_RUN",
                Incident.state.in_(live),
                _resolved_run_condition(),
            )
            .order_by(Incident.created_at, Incident.id)
            .with_for_update(of=Incident, skip_locked=True)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    closed = 0
    for incident in candidates:
        current: Incident | None = incident
        if current.state == IncidentState.OPEN.value:
            current = _transition_incident(
                db, current, expected_state=IncidentState.OPEN, next_state=IncidentState.RETRYING
            )
        if current is None:
            continue
        current = _transition_incident(
            db,
            current,
            expected_state=IncidentState.RETRYING,
            next_state=IncidentState.RECOVERED,
            recovered=True,
        )
        if current is None:
            continue
        acknowledged = _transition_incident(
            db,
            current,
            expected_state=IncidentState.RECOVERED,
            next_state=IncidentState.ACKNOWLEDGED,
            acknowledged=True,
        )
        _audit(
            db,
            acknowledged or current,
            "incident_recovered_by_later_success",
            detail_extra={"slack_suppressed": True},
        )
        closed += 1
    return closed


def _incident_key(run_id: uuid.UUID) -> str:
    return build_incident_key("worker_task", "operation_run", str(run_id), _FINGERPRINT)


def _recoverable_incident(db: Session, run: OperationRun) -> Incident | None:
    """이 실행의 성공이 닫아야 할 사고 한 건.

    일반 경로는 자기 run이 연 BACKGROUND_TASK_FAILED다. sweep이 주인인 작업은 시도마다
    사고를 열지 않으므로, 운영자가 실패한 실행을 운영센터에서 다시 시도해 자식 run이
    성공하면 sweep이 남긴 병원 단위 최종 차단을 닫아야 한다 — 아무도 닫지 않으면 이미
    해결된 일이 사람의 할 일 목록에 영원히 남는다.
    """

    live = (IncidentState.OPEN.value, IncidentState.RETRYING.value)
    if run.operation_type in _SWEEP_OWNED_OPERATIONS:
        if run.hospital_id is None:
            return None
        return db.scalar(
            select(Incident).where(
                Incident.dedupe_key == site_build_incident_key(run.hospital_id),
                Incident.state.in_(live),
            )
        )
    return db.scalar(
        select(Incident).where(
            Incident.dedupe_key == _incident_key(run.id),
            Incident.operation_run_id == run.id,
            Incident.state.in_(live),
        )
    )


def _open_incident(db: Session, run: OperationRun) -> Incident:
    now = datetime.now(UTC)
    impact, action = _operator_copy(run.operation_type)
    statement = (
        insert(Incident)
        .values(
            id=uuid.uuid4(),
            hospital_id=run.hospital_id,
            operation_run_id=run.id,
            dedupe_key=_incident_key(run.id),
            incident_type="BACKGROUND_TASK_FAILED",
            state=IncidentState.OPEN.value,
            severity=IncidentSeverity.HIGH.value,
            customer_impact=impact,
            source_type="OPERATION_RUN",
            source_id=str(run.id),
            safe_error_code="TASK_FAILED",
            safe_error_message="자동 작업이 완료되지 않았습니다.",
            next_action=action,
            admin_path="/operations",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[Incident.dedupe_key],
            set_={
                "state": IncidentState.OPEN.value,
                "operation_run_id": run.id,
                "customer_impact": impact,
                "safe_error_message": "자동 작업이 완료되지 않았습니다.",
                "next_action": action,
                "last_seen_at": now,
                "occurrence_count": Incident.occurrence_count + 1,
                "episode_seq": case(
                    (
                        Incident.state.in_((
                            IncidentState.RECOVERED.value,
                            IncidentState.ACKNOWLEDGED.value,
                        )),
                        Incident.episode_seq + 1,
                    ),
                    else_=Incident.episode_seq,
                ),
                "first_seen_at": case(
                    (
                        Incident.state.in_((
                            IncidentState.RECOVERED.value,
                            IncidentState.ACKNOWLEDGED.value,
                        )),
                        now,
                    ),
                    else_=Incident.first_seen_at,
                ),
                "recovered_at": None,
                "acknowledged_at": None,
                "acknowledged_by_id": None,
                "version": Incident.version + 1,
                "updated_at": now,
            },
        )
        .returning(Incident)
        .execution_options(populate_existing=True)
    )
    return db.execute(statement).scalar_one()


def _transition_incident(
    db: Session,
    incident: Incident,
    *,
    expected_state: IncidentState,
    next_state: IncidentState,
    recovered: bool = False,
    acknowledged: bool = False,
) -> Incident | None:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "state": next_state.value,
        "last_seen_at": now,
        "updated_at": now,
        "version": incident.version + 1,
    }
    if recovered:
        values["recovered_at"] = now
    if acknowledged:
        # NULL owner marks a system acknowledgement, not a person's confirmation.
        values["acknowledged_at"] = now
        values["acknowledged_by_id"] = None
    statement = (
        update(Incident)
        .where(
            Incident.id == incident.id,
            Incident.version == incident.version,
            Incident.state == expected_state.value,
        )
        .values(**values)
        .returning(Incident)
        .execution_options(populate_existing=True)
    )
    return db.execute(statement).scalar_one_or_none()


def _operator_copy(operation_type: str) -> tuple[str, str]:
    normalized = operation_type.upper()
    if any(word in normalized for word in ("REPORT", "DIAGNOSIS", "SOV", "MEASURE")):
        impact = "진단 또는 보고서 결과가 갱신되지 않아 고객 전달 일정이 늦어질 수 있습니다."
    elif any(word in normalized for word in ("CONTENT", "PUBLISH", "IMAGE")):
        impact = "콘텐츠 생성 또는 공개 결과가 반영되지 않아 예정된 운영이 늦어질 수 있습니다."
    elif any(word in normalized for word in ("DOMAIN", "SITE", "CACHE")):
        impact = "병원 공개 화면의 최신 상태 확인이 늦어질 수 있습니다."
    else:
        impact = "요청한 작업 결과가 반영되지 않아 관련 고객 업무가 늦어질 수 있습니다."
    action = (
        "운영 관제에서 이 작업을 열고 ‘작업 다시 시도’를 누르세요. 조치 버튼이 없거나 "
        "다시 실패하면 ‘개발팀 문의용 정보 복사’를 개발팀에 전달하세요."
    )
    return impact, action


def _projection(db: Session, incident: Incident) -> IncidentSlackProjection:
    hospital_name = "시스템 공통 작업"
    if incident.hospital_id is not None:
        hospital_name = db.scalar(
            select(Hospital.name).where(Hospital.id == incident.hospital_id)
        ) or "병원 작업"
    return IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name=hospital_name,
        severity=incident.severity,
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        # 자동 배정된 담당자를 그대로 싣는다 — 주인이 정해진 예외를 Slack이 "미지정"으로
        # 알리면 아무도 자기 일로 보지 않는다.
        owner_label=owner_label_sync(db, incident.owner_id),
        sla_label="확인 필요",
        problem=incident.safe_error_message,
        hospital_id=incident.hospital_id,
        operation_run_id=incident.operation_run_id,
        version=incident.version,
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )


def _enqueue(db: Session, intent: NotificationIntent) -> None:
    validate_message(intent.message, allowed_admin_base_url=settings.ADMIN_BASE_URL)
    now = datetime.now(UTC)
    db.execute(
        insert(NotificationOutbox)
        .values(
            id=uuid.uuid4(),
            hospital_id=intent.hospital_id,
            incident_id=intent.incident_id,
            operation_run_id=intent.operation_run_id,
            dedupe_key=intent.dedupe_key,
            notification_type=intent.notification_type,
            channel=intent.channel,
            state=NotificationOutboxState.PENDING.value,
            payload=intent.message.payload(),
            fallback_text=intent.message.fallback_text,
            max_attempts=intent.max_attempts,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[NotificationOutbox.dedupe_key])
    )


def _audit(
    db: Session,
    incident: Incident,
    action: str,
    *,
    detail_extra: dict[str, str | bool] | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            hospital_id=incident.hospital_id,
            actor="worker",
            action=action,
            target_type="incident",
            target_id=str(incident.id),
            detail={
                "operation_run_id": str(incident.operation_run_id),
                "version": incident.version,
                **(detail_extra or {}),
            },
        )
    )
