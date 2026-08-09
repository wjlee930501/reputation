"""Incident and Slack-outbox projection for content generation failures."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import mark_recovered, mark_retrying, open_or_touch_incident
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import (
    build_open_incident_notification,
    build_recovered_incident_notification,
)
from app.services.notification_store import enqueue_notification


def _fingerprint(code: str) -> IncidentFingerprint:
    match code:
        case "PROVIDER_TIMEOUT":
            return IncidentFingerprint.PROVIDER_TIMEOUT
        case "PROVIDER_UNAVAILABLE" | "GENERATION_REJECTED":
            return IncidentFingerprint.PROVIDER_REJECTED
        case "MISSING_APPROVED_ESSENCE":
            return IncidentFingerprint.MISSING_PREREQUISITE
        case "COST_BLOCKED":
            return IncidentFingerprint.COST_BLOCKED
        case "IMAGE_GENERATION_FAILED":
            return IncidentFingerprint.RENDER_FAILED
        case "GENERATION_LEASE_ACTIVE" | "STALE_GENERATION_CLAIM":
            return IncidentFingerprint.VALIDATION_FAILED
        case _:
            return IncidentFingerprint.UNKNOWN


def _projection(
    incident: Incident, hospital_name: str, run_id: uuid.UUID, owner: str, sla: str
) -> IncidentSlackProjection:
    return IncidentSlackProjection(
        incident.id,
        hospital_name,
        incident.severity,
        incident.customer_impact,
        incident.next_action,
        incident.admin_path,
        owner,
        sla,
        incident.hospital_id,
        run_id,
        incident.version,
    )


async def open_generation_incident(
    *,
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    hospital_name: str,
    run_id: uuid.UUID,
    code: str,
    message: str,
) -> uuid.UUID:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline="content_generation",
                object_type="content_item",
                object_id=str(item_id),
                fingerprint=_fingerprint(code),
                incident_type="CONTENT_GENERATION_FAILED",
                severity=IncidentSeverity.HIGH,
                customer_impact="예정된 콘텐츠 생성이 완료되지 않았습니다.",
                source_type="CONTENT_GENERATION",
                next_action="운영 기준과 공급자 상태를 확인한 뒤 작업을 다시 시도해 주세요.",
                admin_path=f"/hospitals/{hospital_id}/content",
                hospital_id=hospital_id,
                operation_run_id=run_id,
                source_id=str(item_id),
                safe_error_code=code,
                safe_error_message=message,
            ),
            actor="content-generation-worker",
            reason="generation attempt failed",
        )
        await enqueue_notification(
            db,
            build_open_incident_notification(
                _projection(incident, hospital_name, run_id, "미지정", "확인 필요"),
                settings.ADMIN_BASE_URL,
            ),
        )
        await db.commit()
        return incident.id


async def recover_generation_incidents(
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    hospital_name: str,
    run_id: uuid.UUID,
    *,
    include_image: bool = True,
) -> int:
    sessions = get_async_sessionmaker()
    recovered = 0
    async with sessions() as db:
        statement = select(Incident).where(
            Incident.hospital_id == hospital_id,
            Incident.source_type == "CONTENT_GENERATION",
            Incident.source_id == str(item_id),
            Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
        )
        if not include_image:
            statement = statement.where(Incident.safe_error_code != "IMAGE_GENERATION_FAILED")
        incidents = list(
            (
                await db.execute(statement)
            ).scalars()
        )
        for incident in incidents:
            current = incident
            if current.state == IncidentState.OPEN:
                retrying = await mark_retrying(
                    db,
                    current.id,
                    expected_version=current.version,
                    actor="content-generation-worker",
                    reason="generation retry started",
                )
                if not isinstance(retrying, Incident):
                    continue
                current = retrying
            current.operation_run_id = run_id
            result = await mark_recovered(
                db,
                current.id,
                expected_version=current.version,
                observed_success=True,
                actor="content-generation-worker",
                reason="generation retry succeeded",
            )
            if not isinstance(result, Incident):
                continue
            await enqueue_notification(
                db,
                build_recovered_incident_notification(
                    _projection(result, hospital_name, run_id, "미지정", "복구됨"),
                    settings.ADMIN_BASE_URL,
                ),
            )
            recovered += 1
        await db.commit()
    return recovered
