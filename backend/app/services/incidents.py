"""Durable incident lifecycle shared by worker and Admin adapters."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import Incident, IncidentState
from app.services.audit_log import write_audit_log
from app.services.incident_assignment import (
    needs_auto_assign,
    resolve_auto_assign_owner,
)
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
    DEFAULT_RETRYING_DEADLINE,
    IncidentFilters,
    IncidentFingerprint,
    IncidentLabels,
    IncidentMutationResult,
    IncidentNotFound,
    IncidentOpenRequest,
    IncidentTransitionConflict,
    IncidentVersionConflict,
    incident_policy,
    retry_deadline,
)

__all__ = (
    "IncidentFingerprint", "IncidentFilters", "IncidentLabels", "IncidentMutationResult",
    "IncidentNotFound",
    "IncidentOpenRequest", "IncidentTransitionConflict", "IncidentVersionConflict",
    "acknowledge_incident", "assign_incident",
    "auto_acknowledge_incident", "auto_assign_owner",
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
    """Atomically create or reopen one logical incident and preserve its first fact.

    스윕이 재시도를 소유하는 종류(`incident_types.INCIDENT_TYPE_POLICIES`의 sweep_owned)는 기한이
    남아 있으면 RETRYING+기한으로 열린다 — 기한 안의 자동 재시도는 사람의 일이 아니다. 기한은
    에피소드 시작에서 계산하므로 터치마다 밀리지 않는다. 이미 OPEN인 행(사람이 가진 일)은 내리지
    않는다. 기한이 지났거나 정할 수 없으면 종전처럼 OPEN이다. 등록 밖 종류는 종전과 같다.
    """

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
    dedupe_key = build_incident_key(
        request.pipeline, request.object_type, request.object_id, request.fingerprint
    )
    retry_until = await _sweep_retry_deadline(db, request, dedupe_key, observed_at)
    # 조용한 종류(Slack·할 일·요약 건수에서 빠지는 관찰)는 언제나 LOW다 — 호출부의 기본값
    # (`open_ops_incident`의 HIGH)이 OPEN/HIGH 더미를 만들지 않게 한다(Full-Automatic W9, 설계 §3.6).
    quiet = incident_policy(request.incident_type).quiet
    requested_severity = "LOW" if quiet else request.severity.value
    base = insert(Incident).values(
        id=uuid.uuid4(),
        hospital_id=request.hospital_id,
        operation_run_id=request.operation_run_id,
        dedupe_key=dedupe_key,
        incident_type=normalize_incident_code(request.incident_type),
        state=(
            IncidentState.RETRYING if retry_until is not None else IncidentState.OPEN
        ).value,
        sla_due_at=retry_until,
        severity=requested_severity,
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
    if quiet:
        # 이미 HIGH로 열린 옛 행도 다음 관측에서 LOW로 내린다.
        severity = literal("LOW", type_=Incident.severity.type)
    reopened = Incident.state.in_(
        (IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value)
    )
    touch: dict[str, object] = {
        "state": _touched_state(retry_until),
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
        "episode_seq": case((reopened, Incident.episode_seq + 1), else_=Incident.episode_seq),
        # Episode start. Reset only on reopen so 48h promotion can fire again.
        "first_seen_at": case((reopened, observed_at), else_=Incident.first_seen_at),
        "recovered_at": None,
        "acknowledged_at": None,
        "acknowledged_by_id": None,
        "version": Incident.version + 1,
        "updated_at": observed_at,
    }
    if retry_until is not None:
        # 사람이 가진 OPEN의 기한은 건드리지 않는다. 스윕이 가진 행만 기한을 맞춘다.
        touch["sla_due_at"] = case(
            (Incident.state == IncidentState.OPEN.value, Incident.sla_due_at),
            else_=literal(retry_until, type_=Incident.sla_due_at.type),
        )
    elif incident_policy(request.incident_type).sweep_owned:
        # 스윕 소유 종류가 OPEN으로 넘어가면(기한 경과·사람의 일인 원인) 지난 자동 재시도 기한을 지운다.
        # 이미 OPEN인 행은 사람이 정한 기한일 수 있어 그대로 둔다(`assign_incident`).
        touch["sla_due_at"] = case(
            (Incident.state == IncidentState.OPEN.value, Incident.sla_due_at),
            else_=None,
        )
    statement = base.on_conflict_do_update(
        index_elements=[Incident.dedupe_key], set_=touch
    ).returning(Incident).execution_options(populate_existing=True)
    incident = (await db.execute(statement)).scalar_one()
    await _audit(db, incident, actor, "incident_occurrence_recorded", reason)
    return await auto_assign_owner(db, incident, observed_at=observed_at, actor=actor)


async def _sweep_retry_deadline(
    db: AsyncSession, request: IncidentOpenRequest, dedupe_key: str, observed_at: datetime
) -> datetime | None:
    """스윕 소유 종류면 이 에피소드의 남은 기한. 아니면 None(한 번의 조회도 하지 않는다)."""

    if not incident_policy(request.incident_type).sweep_owned:
        return None
    existing = (
        await db.execute(
            select(Incident.state, Incident.first_seen_at).where(
                Incident.dedupe_key == dedupe_key
            )
        )
    ).one_or_none()
    live = existing is not None and existing.state in (
        IncidentState.OPEN.value,
        IncidentState.RETRYING.value,
    )
    episode_start = existing.first_seen_at if live else observed_at
    return retry_deadline(request, episode_start=episode_start, observed_at=observed_at)


def _touched_state(retry_until: datetime | None):
    """충돌 갱신의 상태. 기한이 남은 스윕 소유 종류는 RETRYING이되, 사람이 가진 OPEN은 내리지 않는다."""

    if retry_until is None:
        return IncidentState.OPEN.value
    return case(
        (Incident.state == IncidentState.OPEN.value, IncidentState.OPEN.value),
        else_=IncidentState.RETRYING.value,
    )


async def auto_assign_owner(
    db: AsyncSession,
    incident: Incident,
    *,
    observed_at: datetime,
    actor: str = "system",
) -> Incident:
    """새 에피소드가 열릴 때 담당자를 정해 둔다 (H-15).

    후보 규칙과 "첫 open만" 조건은 worker 경로와 공유한다
    (`services/incident_assignment`). 여기서만 다른 것은 배정을 낙관적 잠금으로
    기록하고 감사 로그를 남긴다는 점뿐이다.
    """
    if not needs_auto_assign(incident.owner_id, incident.first_seen_at, observed_at):
        return incident
    owner_id = await resolve_auto_assign_owner(db, incident.hospital_id)
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
    db: AsyncSession,
    incident_id: uuid.UUID,
    *,
    expected_version: int,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> IncidentMutationResult:
    """OPEN→RETRYING. 기한 없는 RETRYING은 주인 없는 '복구 중'이므로 기본 기한(24시간)을 단다.

    미래 기한이 이미 있으면 그대로 둔다. 호출자는 돌려받은 행에 더 정확한 기한을 덮어쓸 수 있고,
    시스템이 끝까지 소유하는 경우(MISSING_APPROVED_ESSENCE — Essence 자동 적용, D33)는 None으로
    지울 수 있다.
    """

    changed_at = now or datetime.now(UTC)
    deadline = case(
        (Incident.sla_due_at > changed_at, Incident.sla_due_at),
        else_=literal(changed_at + DEFAULT_RETRYING_DEADLINE, type_=Incident.sla_due_at.type),
    )
    return await _transition(
        db, incident_id, expected_version, IncidentState.OPEN, IncidentState.RETRYING,
        actor, "incident_retrying", reason, {"sla_due_at": deadline}, changed_at,
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
    extra: dict[str, object] | None = None,
    now: datetime | None = None,
) -> IncidentMutationResult:
    values: dict[str, object] = {"state": target.value}
    values.update(extra or {})
    return await _mutate(db, incident_id, expected_version, current, values, actor, action, reason, now)


async def _mutate(
    db: AsyncSession, incident_id: uuid.UUID, expected_version: int,
    required_state: IncidentState | None, values: dict[str, object],
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
