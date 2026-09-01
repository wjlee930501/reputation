"""Structured replacement for legacy best-effort operations alerts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.incident_safety import should_notify_incident_recovery
from app.services.incident_types import (
    IncidentFingerprint,
    IncidentOpenRequest,
    incident_type_of,
)
from app.services.incidents import (
    auto_acknowledge_incident,
    build_incident_key,
    mark_recovered,
    mark_retrying,
    open_or_touch_incident,
)
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import (
    build_open_incident_notification,
    build_recovered_incident_notification,
)
from app.services.notification_store import enqueue_notification


async def open_ops_incident(
    *,
    pipeline: str,
    object_type: str,
    object_id: str,
    incident_type: str,
    safe_error_code: str,
    problem: str,
    customer_impact: str,
    next_action: str,
    source_type: str,
    hospital_name: str = "시스템 공통 작업",
    hospital_id: uuid.UUID | None = None,
    operation_run_id: uuid.UUID | None = None,
    admin_path: str = "/operations",
    fingerprint: IncidentFingerprint = IncidentFingerprint.UNKNOWN,
    actor: str = "system",
    sla_label: str = "확인 필요",
    notify: bool = True,
    severity: IncidentSeverity = IncidentSeverity.HIGH,
) -> uuid.UUID:
    """Open/touch one incident and enqueue only on first-open or reopen."""

    sessions = get_async_sessionmaker()
    async with sessions() as db:
        key = build_incident_key(pipeline, object_type, object_id, fingerprint)
        previous_state = await db.scalar(select(Incident.state).where(Incident.dedupe_key == key))
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline=pipeline,
                object_type=object_type,
                object_id=object_id,
                fingerprint=fingerprint,
                incident_type=incident_type,
                severity=severity,
                customer_impact=customer_impact,
                source_type=source_type,
                next_action=next_action,
                admin_path=admin_path,
                hospital_id=hospital_id,
                operation_run_id=operation_run_id,
                source_id=object_id,
                safe_error_code=safe_error_code,
                safe_error_message=problem,
            ),
            actor=actor,
            reason="operational failure observed",
        )
        if notify and (
            previous_state is None
            or previous_state
            in {
                IncidentState.RECOVERED.value,
                IncidentState.ACKNOWLEDGED.value,
            }
        ):
            await enqueue_notification(
                db,
                build_open_incident_notification(
                    IncidentSlackProjection(
                        incident_id=incident.id,
                        hospital_name=hospital_name,
                        severity=incident.severity,
                        customer_impact=incident.customer_impact,
                        next_action=incident.next_action,
                        admin_path=incident.admin_path,
                        owner_label="미지정",
                        sla_label=sla_label,
                        hospital_id=incident.hospital_id,
                        operation_run_id=incident.operation_run_id,
                        version=incident.version,
                        problem=incident.safe_error_message or problem,
                        episode_seq=incident.episode_seq,
                        incident_type=incident_type_of(incident),
                    ),
                    settings.ADMIN_BASE_URL,
                ),
            )
        await db.commit()
        return incident.id


async def recover_ops_incident(
    *,
    pipeline: str,
    object_type: str,
    object_id: str,
    fingerprint: IncidentFingerprint,
    hospital_name: str = "시스템 공통 작업",
    actor: str = "system",
    reason: str = "automatic recovery observed",
    notify: bool = True,
) -> bool:
    """Recover one exact incident only after this caller observed success."""

    sessions = get_async_sessionmaker()
    async with sessions() as db:
        key = build_incident_key(pipeline, object_type, object_id, fingerprint)
        incident = await db.scalar(select(Incident).where(Incident.dedupe_key == key))
        if incident is None or incident.state in {
            IncidentState.RECOVERED.value,
            IncidentState.ACKNOWLEDGED.value,
        }:
            return False

        if incident.state == IncidentState.OPEN.value:
            retrying = await mark_retrying(
                db,
                incident.id,
                expected_version=incident.version,
                actor=actor,
                reason=reason,
            )
            if not isinstance(retrying, Incident):
                return False
            incident = retrying
        if incident.state != IncidentState.RETRYING.value:
            return False

        recovered = await mark_recovered(
            db,
            incident.id,
            expected_version=incident.version,
            observed_success=True,
            actor=actor,
            reason=reason,
        )
        if not isinstance(recovered, Incident):
            return False
        await _close_recovered_incident(
            db,
            recovered,
            hospital_name=hospital_name,
            actor=actor,
            reason=reason,
            notify=notify,
        )
        await db.commit()
        return True


async def recover_ops_incidents_for_hospital(
    *,
    hospital_id: uuid.UUID,
    pipeline: str,
    incident_type: str,
    hospital_name: str = "시스템 공통 작업",
    actor: str = "system",
    reason: str = "automatic recovery observed",
    notify: bool = True,
) -> int:
    """Recover active incidents for a hospital regardless of their object ID.

    Snapshot-backed incidents can outlive the snapshot hash that opened them. Their
    durable hospital and pipeline identity is therefore the recovery boundary.
    """

    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incidents = list(
            (
                await db.execute(
                    select(Incident).where(
                        Incident.hospital_id == hospital_id,
                        Incident.dedupe_key.startswith(f"incident:v1:{pipeline}:"),
                        Incident.incident_type == incident_type,
                        Incident.state.in_(
                            (IncidentState.OPEN.value, IncidentState.RETRYING.value)
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        recovered_count = 0
        for incident in incidents:
            current = incident
            if current.state == IncidentState.OPEN.value:
                retrying = await mark_retrying(
                    db,
                    current.id,
                    expected_version=current.version,
                    actor=actor,
                    reason=reason,
                )
                if not isinstance(retrying, Incident):
                    continue
                current = retrying
            if current.state != IncidentState.RETRYING.value:
                continue
            recovered = await mark_recovered(
                db,
                current.id,
                expected_version=current.version,
                observed_success=True,
                actor=actor,
                reason=reason,
            )
            if not isinstance(recovered, Incident):
                continue
            recovered_count += 1
            await _close_recovered_incident(
                db,
                recovered,
                hospital_name=hospital_name,
                actor=actor,
                reason=reason,
                notify=notify,
            )
        await db.commit()
        return recovered_count


async def _close_recovered_incident(
    db: AsyncSession,
    recovered: Incident,
    *,
    hospital_name: str,
    actor: str,
    reason: str,
    notify: bool,
) -> None:
    """Close a machine-recovered incident and page only when a human was involved.

    The recovery Slack asked an operator to click "확인 완료" on work no person ever
    started. The system now acknowledges the incident itself, and only an incident
    that already reached a human keeps an (informational) recovery message.
    """

    now = datetime.now(UTC)
    notify_recovery = notify and should_notify_incident_recovery(recovered, now=now)
    if notify_recovery:
        await enqueue_notification(
            db,
            build_recovered_incident_notification(
                IncidentSlackProjection(
                    incident_id=recovered.id,
                    hospital_name=hospital_name,
                    severity=recovered.severity,
                    customer_impact=recovered.customer_impact,
                    next_action=recovered.next_action,
                    admin_path=recovered.admin_path,
                    owner_label="미지정",
                    sla_label="복구됨",
                    hospital_id=recovered.hospital_id,
                    operation_run_id=recovered.operation_run_id,
                    version=recovered.version,
                    episode_seq=recovered.episode_seq,
                    incident_type=incident_type_of(recovered),
                ),
                settings.ADMIN_BASE_URL,
            ),
        )
    await auto_acknowledge_incident(
        db,
        recovered.id,
        expected_version=recovered.version,
        actor=actor,
        reason=reason,
        now=now,
    )
