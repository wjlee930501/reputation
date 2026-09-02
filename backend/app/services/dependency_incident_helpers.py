"""Shared safe presentation helpers for dependency incidents."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.operations import Incident, NotificationOutbox
from app.services.incident_types import incident_type_of
from app.services.notification_contracts import IncidentSlackProjection


def incident_projection(
    incident: Incident,
    hospital_name: str,
    run_id: uuid.UUID,
    deadline: str,
) -> IncidentSlackProjection:
    return IncidentSlackProjection(
        incident.id,
        hospital_name,
        incident.severity,
        incident.customer_impact,
        incident.next_action,
        incident.admin_path,
        "미지정",
        deadline,
        incident.hospital_id,
        run_id,
        incident.version,
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )


def domain_key(canonical_host: str) -> str:
    return hashlib.sha256(canonical_host.encode()).hexdigest()[:16]


def safe_domain_cause(reason: str) -> str:
    return {
        "timeout": "병원 연결 주소가 제한 시간 안에 응답하지 않았습니다.",
        "tls_or_network_error": "병원 연결 주소에 안전하게 연결하지 못했습니다.",
        "redirect_not_allowed": "병원 연결 주소가 다른 주소로 이동되어 확인을 중단했습니다.",
        "invalid_tenant_marker": "병원 식별 정보를 읽을 수 없습니다.",
        "tenant_marker_mismatch": "연결 주소가 다른 병원 정보를 응답했습니다.",
    }.get(reason, "병원 연결 주소에서 올바른 병원 정보를 확인하지 못했습니다.")


def _open_notice_query(incident_id: uuid.UUID):
    return select(NotificationOutbox.id).where(
        NotificationOutbox.incident_id == incident_id,
        NotificationOutbox.notification_type == "INCIDENT_OPEN",
    )


async def open_notice_exists(db: AsyncSession, incident_id: uuid.UUID) -> bool:
    """Return whether an "운영 확인 필요" notice was ever queued for this incident.

    This is the only condition that decides whether an automatic recovery still owes
    Slack a message. A Slack pair is all-or-nothing: an OPEN that reached the outbox
    will reach the channel, so it must always be followed by its RECOVERED, or the
    channel keeps an "운영 확인 필요" line that nothing ever closes. An incident whose
    OPEN was never queued (already OPEN when it was touched again, or opened with
    `notify=False`) never reached a person, so its recovery stays a log line.
    """

    return (await db.scalar(_open_notice_query(incident_id))) is not None


def open_notice_exists_sync(db: Session, incident_id: uuid.UUID) -> bool:
    """Sync sibling of `open_notice_exists` for Celery signal handlers."""

    return db.scalar(_open_notice_query(incident_id)) is not None
