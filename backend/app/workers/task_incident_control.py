"""Run-correlated incident/outbox projection for generic Celery outcomes."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    JSONValue,
    NotificationOutbox,
    NotificationOutboxState,
    OperationRun,
    OperationRunState,
)
from app.services.dependency_incident_helpers import open_notice_exists_sync
from app.services.incident_assignment import auto_assign_owner_sync, owner_label_sync
from app.services.incident_safety import build_incident_key, site_build_incident_key
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
_SIGNALLED_DOMAIN_OPERATIONS: Final = frozenset(
    {
        "CREATE_LEAD_DIAGNOSIS",
        "GENERATE_CONTENT_ITEM",
        "GENERATE_MONTHLY_REPORT",
        "MONTHLY_SOV_PERIOD",
        "RECERTIFY_PUBLISHED_IMAGE",
        "RECOVER_LEAD_MEASUREMENT",
        "RECOVER_LEAD_REPORT",
        "REGENERATE_CONTENT",
        "REGENERATE_CONTENT_IMAGE",
        "RUN_SOV",
        "SCHEDULED_MONTHLY_REPORT",
        "TRIGGER_V0_REPORT",
    }
)
_DOMAIN_OUTCOME_NAMES: Final = {
    "CREATE_LEAD_DIAGNOSIS": "LEAD_DIAGNOSIS_CREATION",
    "GENERATE_CONTENT_ITEM": "CONTENT_BODY_GENERATION",
    "REGENERATE_CONTENT": "CONTENT_BODY_GENERATION",
    "REGENERATE_CONTENT_IMAGE": "CONTENT_IMAGE_GENERATION",
    "RECERTIFY_PUBLISHED_IMAGE": "CONTENT_IMAGE_CERTIFICATION",
    "GENERATE_MONTHLY_REPORT": "MONTHLY_REPORT",
    "SCHEDULED_MONTHLY_REPORT": "MONTHLY_REPORT",
    "MONTHLY_SOV_PERIOD": "MONTHLY_SOV_FLEET",
    "RUN_SOV": "SOV_MEASUREMENT",
    "RECOVER_LEAD_MEASUREMENT": "LEAD_MEASUREMENT_RECOVERY",
    "RECOVER_LEAD_REPORT": "LEAD_REPORT_RECOVERY",
    "TRIGGER_V0_REPORT": "V0_REPORT",
}
# These task bodies persist a classified exception before raising. Only these
# owners may suppress the transport-level copy; an unowned safe_error_code is
# projected below instead of being silently dropped.
_CLASSIFIED_DOMAIN_OWNERS: Final = {
    "CREATE_LEAD_DIAGNOSIS": "lead_diagnosis_tasks",
    "GENERATE_CONTENT_ITEM": "generation_incident_control",
    "GENERATE_MONTHLY_REPORT": "monthly_artifact_incident_control",
    "RECERTIFY_PUBLISHED_IMAGE": "published_image_recertification",
    "RECOVER_LEAD_MEASUREMENT": "lead_recovery_incidents",
    "RECOVER_LEAD_REPORT": "lead_recovery_incidents",
    "REGENERATE_CONTENT": "generation_incident_control",
    "REGENERATE_CONTENT_IMAGE": "generation_incident_control",
    "RUN_SOV": "weekly_sov_incident_control",
    "TRIGGER_V0_REPORT": "v0_incident_control",
}
_PERIOD_TOKEN: Final = re.compile(r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2]|W(?:0[1-9]|[1-4]\d|5[0-3]))")


@dataclass(frozen=True, slots=True)
class TerminalOutcomeIdentity:
    """Human exception identity for one durable domain outcome."""

    dedupe_key: str
    source_id: str
    target_type: str
    target_id: str
    period: str | None
    cause: str


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
        if run.operation_type == "REBUILD_SITE":
            if _record_site_build_operator_failure(db, run):
                db.commit()
                return True
            return False
        identity = _terminal_outcome_identity(run)
        if identity is None:
            return False
        previous = db.scalar(
            select(Incident).where(Incident.dedupe_key == identity.dedupe_key)
        )
        previous_state = previous.state if previous is not None else None
        previous_run_id = previous.operation_run_id if previous is not None else None
        incident = _open_incident(db, run, identity)
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
        if (
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
        if previous_run_id is not None and previous_run_id != run.id:
            _audit(
                db,
                incident,
                "operation_attempt_superseded",
                detail_extra={
                    "superseded_run_id": str(previous_run_id),
                    "superseding_run_id": str(run.id),
                },
            )
        _audit(db, incident, "operation_terminal_failure_opened")
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
        # The domain outcome recovers when a later attempt for the same identity succeeds.
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


def _terminal_outcome_identity(run: OperationRun) -> TerminalOutcomeIdentity | None:
    """Return the explicit domain outcome owned by a Celery terminal signal."""

    if run.operation_type not in _SIGNALLED_DOMAIN_OPERATIONS:
        return None
    cause = str(run.safe_error_code or "TASK_FAILED")
    if cause != "TASK_FAILED" and run.operation_type in _CLASSIFIED_DOMAIN_OWNERS:
        # The task body already projected its classified domain exception.
        return None
    payload = run.request_payload if isinstance(run.request_payload, Mapping) else {}
    dispatch = payload.get("_dispatch")
    dispatch_map = dispatch if isinstance(dispatch, Mapping) else {}
    target_type = str(
        dispatch_map.get("target_type") or payload.get("source_type") or "hospital"
    )
    target_id = str(
        dispatch_map.get("target_id")
        or payload.get("source_id")
        or run.hospital_id
        or "fleet"
    )
    period = _operation_period(run, payload, dispatch_map)
    context = _domain_context(payload, run.result_summary)
    source_id = "|".join(
        (
            _DOMAIN_OUTCOME_NAMES[run.operation_type],
            str(run.hospital_id or "fleet"),
            target_type,
            target_id,
            period or "none",
            context,
        )
    )
    return TerminalOutcomeIdentity(
        dedupe_key=build_incident_key(
            "operation_terminal",
            target_type,
            f"{source_id}|{cause}",
            _FINGERPRINT,
        ),
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        period=period,
        cause=cause,
    )


def _operation_period(
    run: OperationRun,
    payload: Mapping[str, JSONValue],
    dispatch: Mapping[str, JSONValue],
) -> str | None:
    summary = run.result_summary if isinstance(run.result_summary, Mapping) else {}
    for key in ("measurement_month", "measurement_week"):
        value = summary.get(key)
        if isinstance(value, str) and _PERIOD_TOKEN.fullmatch(value):
            return value
    year, month = summary.get("period_year"), summary.get("period_month")
    if isinstance(year, int) and isinstance(month, int) and 1 <= month <= 12:
        return f"{year:04d}-{month:02d}"
    args = dispatch.get("task_args")
    if isinstance(args, Sequence) and not isinstance(args, (str, bytes)):
        values = tuple(args)
        if len(values) >= 4 and values[1] == "monthly":
            if isinstance(values[2], int) and isinstance(values[3], int):
                return f"{values[2]:04d}-{values[3]:02d}"
        if run.operation_type == "GENERATE_MONTHLY_REPORT" and len(values) >= 3:
            if isinstance(values[1], int) and isinstance(values[2], int):
                return f"{values[1]:04d}-{values[2]:02d}"
    for candidate in (
        payload.get("source_id"),
        run.idempotency_key,
    ):
        if isinstance(candidate, str) and (match := _PERIOD_TOKEN.search(candidate)):
            return match.group(0)
    return None


def _domain_context(
    payload: Mapping[str, JSONValue], summary_value: JSONValue
) -> str:
    summary = summary_value if isinstance(summary_value, Mapping) else {}
    for container in (payload, summary):
        for key in (
            "revision",
            "subject_hash",
            "generation_context_hash",
            "source_snapshot_hash",
        ):
            value = container.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return f"{key}:{value}"
    return "current"


def _recoverable_incident(db: Session, run: OperationRun) -> Incident | None:
    """이 실행의 성공이 닫아야 할 사고 한 건.

    일반 경로는 같은 도메인 결과가 연 OPERATION_TERMINAL_FAILED다. sweep이 주인인 작업은 시도마다
    사고를 열지 않으므로, 운영자가 실패한 실행을 운영센터에서 다시 시도해 자식 run이
    성공하면 sweep이 남긴 병원 단위 최종 차단을 닫아야 한다 — 아무도 닫지 않으면 이미
    해결된 일이 사람의 할 일 목록에 영원히 남는다.
    """

    live = (IncidentState.OPEN.value, IncidentState.RETRYING.value)
    if run.operation_type == "REBUILD_SITE":
        if run.hospital_id is None:
            return None
        return db.scalar(
            select(Incident).where(
                Incident.dedupe_key == site_build_incident_key(run.hospital_id),
                Incident.state.in_(live),
            )
        )
    identity = _terminal_outcome_identity(run)
    if identity is None:
        return None
    return db.scalar(
        select(Incident).where(
            Incident.dedupe_key == identity.dedupe_key,
            Incident.state.in_(live),
        )
    )


def _open_incident(
    db: Session, run: OperationRun, identity: TerminalOutcomeIdentity
) -> Incident:
    now = datetime.now(UTC)
    impact, action = _operator_copy(run.operation_type)
    statement = (
        insert(Incident)
        .values(
            id=uuid.uuid4(),
            hospital_id=run.hospital_id,
            operation_run_id=run.id,
            dedupe_key=identity.dedupe_key,
            incident_type="OPERATION_TERMINAL_FAILED",
            state=IncidentState.OPEN.value,
            severity=IncidentSeverity.HIGH.value,
            customer_impact=impact,
            source_type="OPERATION_OUTCOME",
            source_id=identity.source_id,
            safe_error_code=identity.cause,
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
