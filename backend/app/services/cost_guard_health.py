"""비용 가드가 상한을 확인하지 못하고 통과시킨(fail-open) 사실을 개발 사고로 남긴다.

Redis 장애 때 비용 가드는 가용성을 위해 호출을 허용한다(fail-open). 그동안 비용 상한이 없다는 뜻인데
지금까지는 WARNING 한 줄뿐이라 아무도 몰랐다(2026-10 자율 운영 감사 F8). 이제:

- 프로세스·카테고리마다 첫 fail-open에 ERROR 한 줄(GCP worker ERROR 경보가 이메일로 승격)과 개발 대상
  사고 하나(`COST_GUARD_FAIL_OPEN`)를 남긴다. 사고 키는 카테고리 단위라 여러 프로세스가 같은 사고를 만진다.
- 같은 프로세스에서 그 카테고리의 가드가 다시 Redis로 판정하면 사고를 복구한다.
- 사고 기록 실패는 가드 결정에 영향을 주지 않는다(`cost_guard._enqueue_durable_cost_alert`와 같은 원칙).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.dependency_incident_helpers import open_notice_exists
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

logger = logging.getLogger(__name__)

FAIL_OPEN_INCIDENT_TYPE = "COST_GUARD_FAIL_OPEN"
_ACTOR = "cost-guard"
_RECOVERY_REASON = "cost guard judged with redis again"
# 이 프로세스가 사고로 알린 카테고리. 워커 자식은 50개 작업마다 바뀌므로 자연히 다시 관측된다.
_REPORTED: set[str] = set()


def _request(category: str, error_name: str) -> IncidentOpenRequest:
    return IncidentOpenRequest(
        pipeline="cost_guard",
        object_type="fail_open",
        object_id=category,
        fingerprint=IncidentFingerprint.BROKER_UNAVAILABLE,
        incident_type=FAIL_OPEN_INCIDENT_TYPE,
        severity=IncidentSeverity.HIGH,
        customer_impact="비용 가드가 Redis를 읽지 못해 자동 AI 호출을 상한 확인 없이 허용하고 있습니다.",
        source_type="COST_GUARD",
        next_action="Redis(Memorystore) 상태를 확인해 주세요. 비용 가드가 다시 판정하면 자동으로 닫힙니다.",
        admin_path="/operations",
        source_id=f"{category}:fail_open",
        safe_error_code=FAIL_OPEN_INCIDENT_TYPE,
        safe_error_message=f"category={category} error={error_name}",
    )


def _projection(incident: Incident) -> IncidentSlackProjection:
    return IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name="시스템 공통 작업",
        severity=incident.severity,
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        owner_label="미지정",
        sla_label="확인 필요",
        version=incident.version,
        problem=incident.safe_error_message or "비용 가드가 상한을 확인하지 못했습니다.",
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )


async def _open_fail_open_incident(category: str, error_name: str, *, now: datetime) -> None:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await open_or_touch_incident(
            db, _request(category, error_name), actor=_ACTOR, reason="cost guard failed open", now=now
        )
        # 열림 알림의 dedupe 키는 episode 단위라 여러 프로세스가 불러도 한 번만 쌓인다.
        await enqueue_notification(
            db, build_open_incident_notification(_projection(incident), settings.ADMIN_BASE_URL), now=now
        )
        await db.commit()


async def _recover_fail_open_incident(category: str, *, now: datetime) -> None:
    key = build_incident_key("cost_guard", "fail_open", category, IncidentFingerprint.BROKER_UNAVAILABLE)
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await db.scalar(
            select(Incident).where(
                Incident.dedupe_key == key,
                Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value)),
            )
        )
        if incident is None:
            return
        current: object = incident
        if incident.state == IncidentState.OPEN.value:
            current = await mark_retrying(
                db, incident.id, expected_version=incident.version, actor=_ACTOR, reason=_RECOVERY_REASON
            )
        if not isinstance(current, Incident) or current.state != IncidentState.RETRYING.value:
            return
        recovered = await mark_recovered(
            db,
            current.id,
            expected_version=current.version,
            observed_success=True,
            actor=_ACTOR,
            reason=_RECOVERY_REASON,
            now=now,
        )
        if not isinstance(recovered, Incident):
            return
        # 열림 알림을 보낸 episode에만 복구 알림을 짝짓는다.
        if await open_notice_exists(db, recovered.id):
            await enqueue_notification(
                db,
                build_recovered_incident_notification(_projection(recovered), settings.ADMIN_BASE_URL),
                now=now,
            )
        await auto_acknowledge_incident(
            db, recovered.id, expected_version=recovered.version, actor=_ACTOR, reason=_RECOVERY_REASON, now=now
        )
        await db.commit()


async def report_fail_open(category: str, error_name: str) -> None:
    """fail-open 관측. 프로세스·카테고리당 첫 번째만 ERROR와 사고, 이후는 WARNING."""
    if category in _REPORTED:
        logger.warning("cost_guard fail-open (redis unavailable): category=%s error=%s", category, error_name)
        return
    _REPORTED.add(category)
    logger.error(
        "cost_guard_fail_open category=%s error=%s — 비용 상한 없이 호출을 허용하는 중", category, error_name
    )
    try:
        await _open_fail_open_incident(category, error_name, now=datetime.now(UTC))
    except Exception:  # noqa: BLE001 — 사고 기록 실패가 가드 결정을 바꾸지 않는다.
        logger.warning("cost_guard fail-open incident not recorded: category=%s", category)


async def report_guard_healthy(category: str) -> None:
    """가드가 Redis로 판정했다. 이 프로세스가 fail-open을 알린 카테고리면 사고를 닫는다."""
    if category not in _REPORTED:
        return
    _REPORTED.discard(category)
    try:
        await _recover_fail_open_incident(category, now=datetime.now(UTC))
    except Exception:  # noqa: BLE001 — 복구 기록 실패가 가드 결정을 바꾸지 않는다.
        logger.warning("cost_guard fail-open recovery not recorded: category=%s", category)
