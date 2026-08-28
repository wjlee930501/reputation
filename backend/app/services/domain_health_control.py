"""Durable tenant-domain health history and incident recovery."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.hospital import Hospital
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    OperationRun,
    OperationRunState,
)
from app.services.dependency_incident_helpers import (
    domain_key,
    incident_projection,
    safe_domain_cause,
)
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import mark_recovered, mark_retrying, open_or_touch_incident
from app.services.notification_messages import (
    build_open_incident_notification,
)
from app.services.notification_store import enqueue_notification

_OPERATION_TYPE = "DOMAIN_HEALTH_CHECK"
_SOURCE_TYPE = "DOMAIN_HEALTH"
_RECOVERY_CHECKS = 3


@dataclass(frozen=True, slots=True)
class DomainHealthOutcome:
    recorded: bool
    healthy_streak: int
    incident_opened: bool
    incident_recovered: bool


async def record_domain_health_check(
    *,
    hospital_id: uuid.UUID,
    canonical_host: str,
    healthy: bool,
    safe_reason: str,
    observed_at: datetime | None = None,
) -> DomainHealthOutcome:
    """Append one immutable check and transition only its exact tenant incident."""

    checked_at = observed_at or datetime.now(UTC)
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        hospital = await db.scalar(
            select(Hospital).where(
                Hospital.id == hospital_id,
                Hospital.aeo_domain == canonical_host,
            )
        )
        if hospital is None:
            return DomainHealthOutcome(False, 0, False, False)
        domain_key_value = domain_key(canonical_host)
        bucket = int(checked_at.timestamp()) // 900
        idempotency_key = f"domain-health:{domain_key_value}:{bucket}"
        existing = await db.scalar(
            select(OperationRun).where(
                OperationRun.hospital_id == hospital_id,
                OperationRun.operation_type == _OPERATION_TYPE,
                OperationRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return DomainHealthOutcome(
                False, await _healthy_streak(db, hospital_id, domain_key_value), False, False
            )
        run = OperationRun(
            hospital_id=hospital_id,
            operation_type=_OPERATION_TYPE,
            state=(
                OperationRunState.SUCCEEDED.value if healthy else OperationRunState.FAILED.value
            ),
            idempotency_key=idempotency_key,
            request_payload={"canonical_host": canonical_host},
            result_summary={"marker_valid": healthy, "safe_reason": safe_reason[:100]},
            safe_error_code=None if healthy else "DOMAIN_UNHEALTHY",
            safe_error_message=None if healthy else safe_domain_cause(safe_reason),
            requested_at=checked_at,
            started_at=checked_at,
            completed_at=checked_at,
            total_count=1,
            success_count=1 if healthy else 0,
            failure_count=0 if healthy else 1,
        )
        db.add(run)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return DomainHealthOutcome(False, 0, False, False)

        if not healthy:
            opened = await _open_domain_incident(db, hospital, run, canonical_host, safe_reason)
            await db.commit()
            return DomainHealthOutcome(True, 0, opened, False)

        streak = await _healthy_streak(db, hospital_id, domain_key_value)
        recovered = False
        if streak >= _RECOVERY_CHECKS:
            recovered = await _recover_domain_incident(db, hospital, run, canonical_host)
        await db.commit()
        return DomainHealthOutcome(True, streak, False, recovered)


async def _healthy_streak(db: AsyncSession, hospital_id: uuid.UUID, domain_key: str) -> int:
    rows = list(
        (
            await db.execute(
                select(OperationRun.state)
                .where(
                    OperationRun.hospital_id == hospital_id,
                    OperationRun.operation_type == _OPERATION_TYPE,
                    OperationRun.idempotency_key.like(f"domain-health:{domain_key}:%"),
                )
                .order_by(OperationRun.requested_at.desc(), OperationRun.id.desc())
                .limit(_RECOVERY_CHECKS)
            )
        ).scalars()
    )
    streak = 0
    for state in rows:
        if state != OperationRunState.SUCCEEDED.value:
            break
        streak += 1
    return streak


async def _open_domain_incident(
    db: AsyncSession,
    hospital: Hospital,
    run: OperationRun,
    canonical_host: str,
    safe_reason: str,
) -> bool:
    source_id = f"{hospital.id}:{domain_key(canonical_host)}"
    dedupe_key = build_incident_key(
        "domain_health",
        "hospital_domain",
        source_id,
        IncidentFingerprint.DOMAIN_UNHEALTHY,
    )
    previous = await db.scalar(select(Incident).where(Incident.dedupe_key == dedupe_key))
    # 확인 완료는 이 도메인·원인 episode를 사람이 닫았다는 뜻이다. 같은 health
    # observation이 다시 들어와도 ACK를 OPEN으로 되돌리거나 새 Slack episode로
    # 재활용하지 않는다. 실제 복구 뒤 재발한 RECOVERED episode만 다시 연다.
    if (
        previous is not None
        and previous.state == IncidentState.ACKNOWLEDGED.value
    ):
        return False
    should_notify = previous is None or previous.state == IncidentState.RECOVERED.value
    incident = await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="domain_health",
            object_type="hospital_domain",
            object_id=source_id,
            fingerprint=IncidentFingerprint.DOMAIN_UNHEALTHY,
            incident_type="DOMAIN_UNHEALTHY",
            severity=IncidentSeverity.HIGH,
            customer_impact="환자와 AI가 병원 연결 주소에서 공개 콘텐츠를 열지 못할 수 있습니다.",
            source_type=_SOURCE_TYPE,
            next_action="병원 온보딩의 ‘자기 도메인 연결’에서 ‘DNS 확인하고 운영 시작’을 누르세요. 해결되지 않으면 개발팀 문의용 정보를 전달하세요.",
            admin_path=f"/hospitals/{hospital.id}/onboarding",
            hospital_id=hospital.id,
            operation_run_id=run.id,
            source_id=source_id,
            safe_error_code="DOMAIN_UNHEALTHY",
            safe_error_message=safe_domain_cause(safe_reason),
        ),
        actor="domain-health-worker",
        reason="tenant marker check failed",
        now=run.completed_at,
    )
    if should_notify:
        await enqueue_notification(
            db,
            build_open_incident_notification(
                incident_projection(incident, hospital.name, run.id, "확인 필요"),
                settings.ADMIN_BASE_URL,
            ),
        )
    return should_notify


async def _recover_domain_incident(
    db: AsyncSession,
    hospital: Hospital,
    run: OperationRun,
    canonical_host: str,
) -> bool:
    source_id = f"{hospital.id}:{domain_key(canonical_host)}"
    incident = await db.scalar(
        select(Incident).where(
            Incident.hospital_id == hospital.id,
            Incident.source_type == _SOURCE_TYPE,
            Incident.source_id == source_id,
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
            actor="domain-health-worker",
            reason="three-check recovery confirmation started",
        )
        if not isinstance(retrying, Incident):
            return False
        incident = retrying
    result = await mark_recovered(
        db,
        incident.id,
        expected_version=incident.version,
        observed_success=True,
        actor="domain-health-worker",
        reason="three consecutive tenant markers matched",
        now=run.completed_at,
    )
    if not isinstance(result, Incident):
        return False
    # 자동 확인된 정상 상태가 incident를 닫는 최종 증거다. 인증서 복구와 동일하게
    # 성공 Slack이나 확인 클릭을 요구하지 않는다.
    return True
