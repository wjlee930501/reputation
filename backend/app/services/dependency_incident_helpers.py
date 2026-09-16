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
        problem=getattr(incident, "safe_error_message", None) or "자동 작업이 완료되지 않았습니다.",
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )


def domain_key(canonical_host: str) -> str:
    return hashlib.sha256(canonical_host.encode()).hexdigest()[:16]


def safe_domain_cause(reason: str) -> str:
    if reason.startswith("http_") and reason[5:].isdigit():
        status = int(reason[5:])
        if 500 <= status <= 599:
            return f"병원 공개 서비스가 서버 오류(HTTP {status})를 반환했습니다. DNS 설정 오류로 확정된 것은 아닙니다."
    return {
        "timeout": "병원 연결 주소가 제한 시간 안에 응답하지 않았습니다. DNS 장애로 확정된 것은 아닙니다.",
        "http_429": "병원 연결 주소의 점검 요청이 요청 제한(HTTP 429) 응답을 받았습니다.",
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


def safe_domain_next_action(reason: str) -> str:
    """Route the next action by observed failure, not an unproven DNS diagnosis."""
    if reason == "http_429":
        return "DNS 설정을 변경하기 전에 운영 센터에서 요청 제한과 보안 정책을 확인해 주세요. 재점검에서도 반복되면 개발팀에 문의용 정보를 전달하세요."
    if reason.startswith("http_") or reason == "timeout":
        return "병원 공개 주소의 접속 상태를 확인해 주세요. 점검 실패만으로 DNS를 변경하지 말고, 반복되면 개발팀에 서버 응답과 문의용 정보를 전달하세요."
    if reason in {"tenant_marker_mismatch", "invalid_tenant_marker", "redirect_not_allowed"}:
        return "연결 주소가 올바른 병원 페이지로 연결되는지 확인해 주세요. 다른 병원이나 다른 주소가 나오면 개발팀에 문의용 정보를 전달하세요."
    return "병원 온보딩의 자기 도메인 연결에서 DNS와 인증서 상태를 확인해 주세요. 안전하게 연결되지 않거나 같은 문제가 반복되면 개발팀에 문의용 정보를 전달하세요."
