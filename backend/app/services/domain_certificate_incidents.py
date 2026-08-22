"""HTTPS 인증서 영구 실패를 사람에게 알리는 인시던트.

인증서가 끝내 발급되지 않으면 병원 연결 주소는 https로 열리지 않는다 — 시스템이
더 할 수 있는 일이 없고 사람이 지금 DNS를 봐야 하는 상황이다. 그런 경우에만 Slack
1건을 보낸다. 발급 성공은 알리지 않는다(성공 알림 금지). 인시던트 정체성은
병원+도메인 단위라, 원인이 무엇이든 한 도메인의 실패는 한 건으로 모인다.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.dependency_incident_helpers import domain_key, incident_projection
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import mark_recovered, mark_retrying, open_or_touch_incident
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification

CERTIFICATE_INCIDENT_PIPELINE = "domain_certificate"
CERTIFICATE_INCIDENT_OBJECT_TYPE = "hospital_domain"
CERTIFICATE_INCIDENT_SOURCE_TYPE = "DOMAIN_CERTIFICATE"
CERTIFICATE_INCIDENT_ACTOR = "domain-certificate-worker"


class CertificateFailureCause(StrEnum):
    PROVIDER_REFUSED = "CERTIFICATE_PROVIDER_REFUSED"
    BUDGET_EXHAUSTED = "CERTIFICATE_ISSUANCE_TIMED_OUT"


_PROBLEMS: dict[CertificateFailureCause, str] = {
    CertificateFailureCause.PROVIDER_REFUSED: "병원 연결 주소의 HTTPS 인증서 발급이 거부됐습니다.",
    CertificateFailureCause.BUDGET_EXHAUSTED: (
        "병원 연결 주소의 HTTPS 인증서가 예상 시간 안에 발급되지 않았습니다."
    ),
}

_NEXT_ACTION = (
    "도메인 DNS 설정을 확인한 뒤 병원 온보딩의 ‘자기 도메인 연결’에서 "
    "‘DNS 확인하고 운영 시작’을 다시 누르세요."
)
_CUSTOMER_IMPACT = "환자와 AI가 병원 연결 주소를 https로 열지 못합니다."


def certificate_incident_object_id(hospital_id: uuid.UUID, domain: str) -> str:
    """병원+도메인 하나를 가리키는 안전한 식별자.

    도메인을 그대로 쓰면 점 때문에 `normalize_source_id`가 해시로 바꿔 버려서, 저장된
    행을 다시 찾을 수 없다. 도메인 해시를 쓰는 domain_health와 같은 형식을 따른다.
    """
    return f"{hospital_id}:{domain_key(domain)}"


async def open_certificate_failure_incident(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID,
    hospital_name: str,
    domain: str,
    cause: CertificateFailureCause,
) -> bool:
    """인시던트를 열고, 처음 열리거나 다시 열릴 때만 Slack을 큐잉한다."""

    object_id = certificate_incident_object_id(hospital_id, domain)
    dedupe_key = build_incident_key(
        CERTIFICATE_INCIDENT_PIPELINE,
        CERTIFICATE_INCIDENT_OBJECT_TYPE,
        object_id,
        IncidentFingerprint.DOMAIN_UNHEALTHY,
    )
    previous = await db.scalar(select(Incident).where(Incident.dedupe_key == dedupe_key))
    should_notify = previous is None or previous.state in {
        IncidentState.RECOVERED.value,
        IncidentState.ACKNOWLEDGED.value,
    }
    incident = await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline=CERTIFICATE_INCIDENT_PIPELINE,
            object_type=CERTIFICATE_INCIDENT_OBJECT_TYPE,
            object_id=object_id,
            fingerprint=IncidentFingerprint.DOMAIN_UNHEALTHY,
            incident_type="DOMAIN_CERTIFICATE_FAILED",
            severity=IncidentSeverity.HIGH,
            customer_impact=_CUSTOMER_IMPACT,
            source_type=CERTIFICATE_INCIDENT_SOURCE_TYPE,
            next_action=_NEXT_ACTION,
            admin_path=f"/hospitals/{hospital_id}/onboarding",
            hospital_id=hospital_id,
            source_id=object_id,
            safe_error_code=cause.value,
            safe_error_message=_PROBLEMS[cause],
        ),
        actor=CERTIFICATE_INCIDENT_ACTOR,
        reason="certificate provisioning ended without a usable certificate",
    )
    if should_notify:
        await enqueue_notification(
            db,
            build_open_incident_notification(
                incident_projection(incident, hospital_name, None, "확인 필요"),
                settings.ADMIN_BASE_URL,
            ),
        )
    return should_notify


async def recover_certificate_failure_incident(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID,
    domain: str,
) -> bool:
    """발급에 성공하면 열려 있던 인시던트를 닫는다 — 알림은 보내지 않는다."""

    object_id = certificate_incident_object_id(hospital_id, domain)
    incident = await db.scalar(
        select(Incident).where(
            Incident.hospital_id == hospital_id,
            Incident.source_type == CERTIFICATE_INCIDENT_SOURCE_TYPE,
            Incident.source_id == object_id,
            Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value)),
        )
    )
    if incident is None:
        return False
    if incident.state == IncidentState.OPEN.value:
        retrying = await mark_retrying(
            db,
            incident.id,
            expected_version=incident.version,
            actor=CERTIFICATE_INCIDENT_ACTOR,
            reason="certificate provisioning retried",
        )
        if not isinstance(retrying, Incident):
            return False
        incident = retrying
    recovered = await mark_recovered(
        db,
        incident.id,
        expected_version=incident.version,
        observed_success=True,
        actor=CERTIFICATE_INCIDENT_ACTOR,
        reason="certificate became active",
    )
    return isinstance(recovered, Incident)
