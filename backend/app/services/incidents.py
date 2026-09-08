"""Durable incident lifecycle shared by worker and Admin adapters."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_user import ROLE_OWNER, AdminUser
from app.models.handoff import HospitalHandoff
from app.models.operations import Incident, IncidentState
from app.services.audit_log import write_audit_log
from app.services.incident_safety import (
    build_incident_key,
    incident_filter_expressions,
    normalize_admin_path,
    normalize_incident_code,
    normalize_source_id,
    project_incident_labels,
    sanitize_operator_text,
)
from app.services.incident_types import (
    IncidentFilters,
    IncidentFingerprint,
    IncidentLabels,
    IncidentMutationResult,
    IncidentNotFound,
    IncidentOpenRequest,
    IncidentTransitionConflict,
    IncidentVersionConflict,
)

__all__ = (
    "IncidentFingerprint", "IncidentFilters", "IncidentLabels", "IncidentMutationResult",
    "IncidentNotFound",
    "IncidentOpenRequest", "IncidentTransitionConflict", "IncidentVersionConflict",
    "acknowledge_incident", "assign_incident",
    "auto_acknowledge_incident",
    "build_incident_key",
    "incident_filter_expressions",
    "mark_recovered", "mark_retrying", "open_or_touch_incident",
    "project_incident_labels", "sanitize_operator_text",
)


async def open_or_touch_incident(
    db: AsyncSession,
    request: IncidentOpenRequest,
    *,
    actor: str = "system",
    reason: str = "failure observed",
    now: datetime | None = None,
) -> Incident:
    """Atomically create or reopen one logical incident and preserve its first fact."""

    observed_at = now or datetime.now(UTC)
    # Every incident is, by definition, an exceptional operation.  Keep a durable
    # machine key and operator-safe explanation even when an older caller only
    # supplied the incident type/customer impact.  Operations grouping must never
    # have to infer its key from Korean display copy (FN-04).
    safe_error_code = normalize_incident_code(
        request.safe_error_code or request.incident_type
    )
    safe_error_message = sanitize_operator_text(
        request.safe_error_message or request.customer_impact
    ) or "운영 작업이 완료되지 않은 원인을 확인해야 합니다."
    base = insert(Incident).values(
        id=uuid.uuid4(),
        hospital_id=request.hospital_id,
        operation_run_id=request.operation_run_id,
        dedupe_key=build_incident_key(
            request.pipeline, request.object_type, request.object_id, request.fingerprint
        ),
        incident_type=normalize_incident_code(request.incident_type),
        state=IncidentState.OPEN.value,
        severity=request.severity.value,
        customer_impact=sanitize_operator_text(request.customer_impact) or "고객 영향 정보를 아직 확인하지 못했습니다.",
        source_type=normalize_incident_code(request.source_type),
        source_id=normalize_source_id(request.source_id),
        safe_error_code=safe_error_code,
        safe_error_message=safe_error_message,
        next_action=sanitize_operator_text(request.next_action) or "상세 화면에서 원인을 확인하고, 조치 버튼이 없으면 개발팀 문의용 정보를 전달하세요.",
        admin_path=normalize_admin_path(request.admin_path),
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        created_at=observed_at,
        updated_at=observed_at,
    )
    severity = case(
        (or_(Incident.severity == "CRITICAL", base.excluded.severity == "CRITICAL"), "CRITICAL"),
        (or_(Incident.severity == "HIGH", base.excluded.severity == "HIGH"), "HIGH"),
        (or_(Incident.severity == "MEDIUM", base.excluded.severity == "MEDIUM"), "MEDIUM"),
        else_="LOW",
    )
    statement = base.on_conflict_do_update(
        index_elements=[Incident.dedupe_key],
        set_={
            "state": IncidentState.OPEN.value,
            "severity": severity,
            "operation_run_id": func.coalesce(
                base.excluded.operation_run_id, Incident.operation_run_id
            ),
            "customer_impact": base.excluded.customer_impact,
            "safe_error_code": func.coalesce(
                base.excluded.safe_error_code, Incident.safe_error_code
            ),
            "safe_error_message": func.coalesce(
                base.excluded.safe_error_message, Incident.safe_error_message
            ),
            "next_action": base.excluded.next_action,
            "admin_path": base.excluded.admin_path,
            "last_seen_at": observed_at,
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
            # Episode start. Reset only on reopen so 48h promotion can fire again.
            "first_seen_at": case(
                (
                    Incident.state.in_((
                        IncidentState.RECOVERED.value,
                        IncidentState.ACKNOWLEDGED.value,
                    )),
                    observed_at,
                ),
                else_=Incident.first_seen_at,
            ),
            "recovered_at": None,
            "acknowledged_at": None,
            "acknowledged_by_id": None,
            "version": Incident.version + 1,
            "updated_at": observed_at,
        },
    ).returning(Incident).execution_options(populate_existing=True)
    incident = (await db.execute(statement)).scalar_one()
    await _audit(db, incident, actor, "incident_occurrence_recorded", reason)
    return await _auto_assign(db, incident, observed_at, actor)


async def _auto_assign(
    db: AsyncSession, incident: Incident, observed_at: datetime, actor: str
) -> Incident:
    """새 에피소드가 열릴 때 담당자를 정해 둔다 (H-15).

    주인 없는 예외는 아무도 자기 일로 보지 않는다. 병원의 계약 인수 AE가 1순위이고,
    없으면 활성 OWNER 한 명이 받는다. 이미 담당자가 있으면 절대 덮지 않는다 — 재발로
    다시 열린 건은 그 사람이 계속 본다. 후보가 없으면 그대로 비워 둔다(실패 아님).

    `first_seen_at`은 새 행과 재open에서만 이번 관측 시각으로 맞춰지므로(위 upsert),
    같은 에피소드의 반복 관측에서는 이 조회가 아예 돌지 않는다.
    """
    if incident.owner_id is not None or incident.first_seen_at != observed_at:
        return incident
    owner_id: uuid.UUID | None = None
    if incident.hospital_id is not None:
        owner_id = await db.scalar(
            select(HospitalHandoff.ae_owner_id).where(
                HospitalHandoff.hospital_id == incident.hospital_id
            )
        )
    if owner_id is None:
        owner_id = await db.scalar(
            select(AdminUser.id)
            .where(
                AdminUser.role == ROLE_OWNER,
                AdminUser.is_active.is_(True),
                AdminUser.is_operations_test.is_(False),
            )
            .order_by(AdminUser.created_at.asc(), AdminUser.id.asc())
            .limit(1)
        )
    if owner_id is None:
        return incident
    assigned = await _mutate(
        db,
        incident.id,
        incident.version,
        None,
        {"owner_id": owner_id},
        actor,
        "incident_assigned",
        "auto-assigned on first open",
        observed_at,
        detail_extra={"auto_assigned": True, "auto_assigned_to": str(owner_id)},
    )
    return assigned if isinstance(assigned, Incident) else incident


async def assign_incident(
    db: AsyncSession,
    incident_id: uuid.UUID,
    *,
    expected_version: int,
    owner_id: uuid.UUID | None,
    sla_due_at: datetime | None,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> IncidentMutationResult:
    """Assign ownership/SLA without changing the incident lifecycle state."""

    return await _mutate(
        db,
        incident_id,
        expected_version,
        None,
        {"owner_id": owner_id, "sla_due_at": sla_due_at},
        actor,
        "incident_assigned",
        reason,
        now,
    )


async def mark_retrying(
    db: AsyncSession, incident_id: uuid.UUID, *, expected_version: int, actor: str, reason: str
) -> IncidentMutationResult:
    return await _transition(
        db, incident_id, expected_version, IncidentState.OPEN, IncidentState.RETRYING,
        actor, "incident_retrying", reason
    )


async def mark_recovered(
    db: AsyncSession,
    incident_id: uuid.UUID,
    *,
    expected_version: int,
    observed_success: bool,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> IncidentMutationResult:
    if not observed_success:
        return await _transition_error(
            db,
            incident_id,
            expected_version,
            "INCIDENT_RECOVERY_NOT_OBSERVED",
            "RETRYING",
        )
    observed_at = now or datetime.now(UTC)
    return await _transition(
        db, incident_id, expected_version, IncidentState.RETRYING, IncidentState.RECOVERED,
        actor, "incident_recovered", reason, {"recovered_at": observed_at}, observed_at
    )


async def acknowledge_incident(
    db: AsyncSession,
    incident_id: uuid.UUID,
    *,
    expected_version: int,
    acknowledged_by_id: uuid.UUID,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> IncidentMutationResult:
    acknowledged_at = now or datetime.now(UTC)
    return await _transition(
        db, incident_id, expected_version, IncidentState.RECOVERED,
        IncidentState.ACKNOWLEDGED, actor, "incident_acknowledged", reason,
        {"acknowledged_at": acknowledged_at, "acknowledged_by_id": acknowledged_by_id},
        acknowledged_at,
    )


async def auto_acknowledge_incident(
    db: AsyncSession,
    incident_id: uuid.UUID,
    *,
    expected_version: int,
    actor: str = "system",
    reason: str = "automatic recovery closed the incident",
    now: datetime | None = None,
) -> IncidentMutationResult:
    """Close a machine-recovered incident without asking a person to confirm it.

    A system acknowledgement leaves ``acknowledged_by_id`` NULL. That NULL is the
    durable marker separating "the machine closed this" from "a person read and
    closed this", so callers that must preserve a human decision can still tell
    the two apart.
    """

    acknowledged_at = now or datetime.now(UTC)
    return await _transition(
        db, incident_id, expected_version, IncidentState.RECOVERED,
        IncidentState.ACKNOWLEDGED, actor, "incident_auto_acknowledged", reason,
        {"acknowledged_at": acknowledged_at, "acknowledged_by_id": None},
        acknowledged_at,
    )


async def _transition(
    db: AsyncSession, incident_id: uuid.UUID, expected_version: int,
    current: IncidentState, target: IncidentState, actor: str, action: str, reason: str,
    extra: dict[str, str | uuid.UUID | datetime | None] | None = None,
    now: datetime | None = None,
) -> IncidentMutationResult:
    values: dict[str, str | uuid.UUID | datetime | None] = {"state": target.value}
    values.update(extra or {})
    return await _mutate(db, incident_id, expected_version, current, values, actor, action, reason, now)


async def _mutate(
    db: AsyncSession, incident_id: uuid.UUID, expected_version: int,
    required_state: IncidentState | None, values: dict[str, str | uuid.UUID | datetime | None],
    actor: str, action: str, reason: str, now: datetime | None,
    *, detail_extra: dict[str, str | bool] | None = None,
) -> IncidentMutationResult:
    changed_at = now or datetime.now(UTC)
    predicates = [Incident.id == incident_id, Incident.version == expected_version]
    if required_state is not None:
        predicates.append(Incident.state == required_state.value)
    statement = update(Incident).where(*predicates).values(
        **values, version=Incident.version + 1, updated_at=changed_at
    ).returning(Incident).execution_options(populate_existing=True)
    incident = (await db.execute(statement)).scalar_one_or_none()
    if incident is None:
        return await _conflict(db, incident_id, expected_version, required_state)
    await _audit(db, incident, actor, action, reason, detail_extra)
    return incident


async def _conflict(
    db: AsyncSession, incident_id: uuid.UUID, expected: int, required: IncidentState | None
) -> IncidentMutationResult:
    current = await db.scalar(
        select(Incident).where(Incident.id == incident_id).execution_options(populate_existing=True)
    )
    if current is None:
        return IncidentNotFound("INCIDENT_NOT_FOUND", incident_id)
    if current.version != expected:
        return IncidentVersionConflict(
            "INCIDENT_VERSION_CONFLICT", incident_id, expected, current.version, current.state
        )
    return IncidentTransitionConflict(
        "INCIDENT_TRANSITION_CONFLICT", incident_id, current.version, current.state,
        required.value if required else current.state,
    )


async def _transition_error(
    db: AsyncSession,
    incident_id: uuid.UUID,
    expected_version: int,
    code: str,
    required: str,
) -> IncidentMutationResult:
    current = await db.get(Incident, incident_id)
    if current is None:
        return IncidentNotFound("INCIDENT_NOT_FOUND", incident_id)
    if current.version != expected_version:
        return IncidentVersionConflict(
            "INCIDENT_VERSION_CONFLICT",
            incident_id,
            expected_version,
            current.version,
            current.state,
        )
    return IncidentTransitionConflict(code, incident_id, current.version, current.state, required)


async def _audit(
    db: AsyncSession, incident: Incident, actor: str, action: str, reason: str | None,
    detail_extra: dict[str, str | bool] | None = None,
) -> None:
    await write_audit_log(
        db, action=action, hospital_id=incident.hospital_id, actor=actor,
        target_type="incident", target_id=incident.id,
        detail={"state": incident.state, "version": incident.version,
                "occurrence_count": incident.occurrence_count,
                "reason": sanitize_operator_text(reason, limit=200) if reason else None,
                **(detail_extra or {})},
    )
    await db.flush()
