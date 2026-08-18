"""Run-correlated incident/outbox projection for generic Celery outcomes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

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
    NotificationOutbox,
    NotificationOutboxState,
    OperationRun,
)
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint
from app.services.notification_contracts import (
    IncidentSlackProjection,
    NotificationIntent,
    validate_message,
)
from app.services.notification_messages import (
    build_open_incident_notification,
    build_recovered_incident_notification,
)

_FINGERPRINT = IncidentFingerprint.UNKNOWN


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
        previous_state = db.scalar(
            select(Incident.state).where(Incident.dedupe_key == _incident_key(run.id))
        )
        incident = _open_incident(db, run)
        if previous_state is None or previous_state in {
            IncidentState.RECOVERED.value,
            IncidentState.ACKNOWLEDGED.value,
        }:
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
        incident = db.scalar(
            select(Incident).where(
                Incident.dedupe_key == _incident_key(run.id),
                Incident.operation_run_id == run.id,
                Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value)),
            )
        )
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
        _enqueue(
            db,
            build_recovered_incident_notification(
                _projection(db, incident), settings.ADMIN_BASE_URL
            ),
        )
        _audit(db, incident, "incident_recovered")
        db.commit()
    return True


def _run_identity(
    task: SignalTask | None, task_id: str | None
) -> tuple[uuid.UUID, str] | None:
    if task is None or task_id is None or not task_id.strip():
        return None
    headers = getattr(task.request, "headers", None)
    if not isinstance(headers, dict):
        return None
    raw = headers.get("operation_run_id")
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw), task_id.strip()
    except ValueError:
        return None


def _tracked_run(db: Session, run_id: uuid.UUID, task_id: str) -> OperationRun | None:
    return db.scalar(
        select(OperationRun).where(OperationRun.id == run_id, OperationRun.task_id == task_id)
    )


def _incident_key(run_id: uuid.UUID) -> str:
    return build_incident_key("worker_task", "operation_run", str(run_id), _FINGERPRINT)


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
        owner_label="미지정",
        sla_label="확인 필요",
        problem=incident.safe_error_message,
        hospital_id=incident.hospital_id,
        operation_run_id=incident.operation_run_id,
        version=incident.version,
        episode_seq=incident.episode_seq,
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


def _audit(db: Session, incident: Incident, action: str) -> None:
    db.add(
        AdminAuditLog(
            hospital_id=incident.hospital_id,
            actor="worker",
            action=action,
            target_type="incident",
            target_id=str(incident.id),
            detail={"operation_run_id": str(incident.operation_run_id), "version": incident.version},
        )
    )
