"""Structured replacement for legacy best-effort operations alerts."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import build_incident_key, open_or_touch_incident
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
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
                severity=IncidentSeverity.HIGH,
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
                    ),
                    settings.ADMIN_BASE_URL,
                ),
            )
        await db.commit()
        return incident.id
