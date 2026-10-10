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
    safe_domain_next_action,
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
# 한 번의 실패로는 열지 않는다. 2026-10-09 12:30 UTC 공개 서비스의 일시적 503 한 번이 세 병원에
# `[조치 필요]`를 열었고, 앞뒤 점검은 모두 정상이었다. 연속 두 번(15분 간격) 실패해야 사람을 부른다.
_FAILURE_CHECKS = 2
_RECOVERY_CHECKS = 3

# 공유 공개 서비스(Site) 한 곳이 여러 병원 주소를 함께 서빙한다. 같은 점검에서 두 곳 이상이 5xx를
# 받으면 병원 문제가 아니라 공개 서비스 문제다 — 병원별 `[조치 필요]` 대신 개발 담당 인시던트 하나를 연다.
PUBLIC_SITE_INCIDENT_TYPE = "PUBLIC_SITE_UNAVAILABLE"
PUBLIC_SITE_OUTAGE_MIN_HOSPITALS = 2
_PUBLIC_SITE_OPERATION_TYPE = "PUBLIC_SITE_HEALTH_CHECK"
_PUBLIC_SITE_SOURCE_TYPE = "PUBLIC_SITE_HEALTH"
_PUBLIC_SITE_SOURCE_ID = "shared-public-site"
_PUBLIC_SITE_KEY_PREFIX = "public-site-health:"


@dataclass(frozen=True, slots=True)
class DomainHealthOutcome:
    recorded: bool
    healthy_streak: int
    incident_opened: bool
    incident_recovered: bool


@dataclass(frozen=True, slots=True)
class PublicSiteOutcome:
    recorded: bool
    incident_opened: bool
    incident_recovered: bool


def is_public_site_server_error(safe_reason: str) -> bool:
    """공유 공개 서비스가 돌려준 5xx인가 — 시간 초과·TLS·마커 불일치는 병원별 원인일 수 있다."""

    code = safe_reason.removeprefix("http_")
    return safe_reason.startswith("http_") and code.isdigit() and 500 <= int(code) <= 599


async def record_domain_health_check(
    *,
    hospital_id: uuid.UUID,
    canonical_host: str,
    healthy: bool,
    safe_reason: str,
    observed_at: datetime | None = None,
    open_incident: bool = True,
) -> DomainHealthOutcome:
    """Append one immutable check and transition only its exact tenant incident.

    실패는 같은 도메인의 연속 `_FAILURE_CHECKS`번째부터 병원 인시던트를 연다. 한 번의 실패는
    기록만 남긴다. `open_incident=False`는 같은 점검에서 여러 병원이 공유 공개 서비스의 5xx를 받은
    경우다 — 기록은 남기되 병원 인시던트는 열지 않고 `record_public_site_check`가 소유한다.
    """

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
            opened = False
            failures = await _failure_streak(db, hospital_id, domain_key_value)
            if open_incident and failures >= _FAILURE_CHECKS:
                opened = await _open_domain_incident(
                    db, hospital, run, canonical_host, safe_reason
                )
            await db.commit()
            return DomainHealthOutcome(True, 0, opened, False)

        streak = await _healthy_streak(db, hospital_id, domain_key_value)
        recovered = False
        if streak >= _RECOVERY_CHECKS:
            recovered = await _recover_domain_incident(db, hospital, run, canonical_host)
        await db.commit()
        return DomainHealthOutcome(True, streak, False, recovered)


async def _state_streak(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID | None,
    operation_type: str,
    key_prefix: str,
    state: str,
    limit: int,
) -> int:
    """How many of the newest checks in one history are in ``state``, up to ``limit``."""

    owner = (
        OperationRun.hospital_id.is_(None)
        if hospital_id is None
        else OperationRun.hospital_id == hospital_id
    )
    rows = list(
        (
            await db.execute(
                select(OperationRun.state)
                .where(
                    owner,
                    OperationRun.operation_type == operation_type,
                    OperationRun.idempotency_key.like(f"{key_prefix}%"),
                )
                .order_by(OperationRun.requested_at.desc(), OperationRun.id.desc())
                .limit(limit)
            )
        ).scalars()
    )
    streak = 0
    for row_state in rows:
        if row_state != state:
            break
        streak += 1
    return streak


async def _healthy_streak(db: AsyncSession, hospital_id: uuid.UUID, domain_key: str) -> int:
    return await _state_streak(
        db,
        hospital_id=hospital_id,
        operation_type=_OPERATION_TYPE,
        key_prefix=f"domain-health:{domain_key}:",
        state=OperationRunState.SUCCEEDED.value,
        limit=_RECOVERY_CHECKS,
    )


async def _failure_streak(db: AsyncSession, hospital_id: uuid.UUID, domain_key: str) -> int:
    return await _state_streak(
        db,
        hospital_id=hospital_id,
        operation_type=_OPERATION_TYPE,
        key_prefix=f"domain-health:{domain_key}:",
        state=OperationRunState.FAILED.value,
        limit=_FAILURE_CHECKS,
    )


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
            next_action=safe_domain_next_action(safe_reason),
            admin_path=f"/hospitals/{hospital.id}/info",
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
    return await _recover_open_incident(
        db,
        hospital_id=hospital.id,
        source_type=_SOURCE_TYPE,
        source_id=f"{hospital.id}:{domain_key(canonical_host)}",
        run=run,
        reason="three consecutive tenant markers matched",
    )


async def _recover_open_incident(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID | None,
    source_type: str,
    source_id: str,
    run: OperationRun,
    reason: str,
) -> bool:
    owner = (
        Incident.hospital_id.is_(None) if hospital_id is None else Incident.hospital_id == hospital_id
    )
    incident = await db.scalar(
        select(Incident).where(
            owner,
            Incident.source_type == source_type,
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
        reason=reason,
        now=run.completed_at,
    )
    if not isinstance(result, Incident):
        return False
    # 자동 확인된 정상 상태가 incident를 닫는 최종 증거다. 인증서 복구와 동일하게
    # 성공 Slack이나 확인 클릭을 요구하지 않는다.
    return True


async def record_public_site_check(
    *,
    failing_hospitals: int,
    observed_at: datetime | None = None,
) -> PublicSiteOutcome:
    """Record one monitor run's shared public-service verdict and transition its one incident.

    한 점검에서 `PUBLIC_SITE_OUTAGE_MIN_HOSPITALS`곳 이상이 5xx면 그 실행은 실패다. 병원 점검과
    같은 규칙으로 연속 두 번 실패해야 인시던트를 열고, 연속 세 번 정상이어야 복구한다.
    """

    checked_at = observed_at or datetime.now(UTC)
    unavailable = failing_hospitals >= PUBLIC_SITE_OUTAGE_MIN_HOSPITALS
    idempotency_key = f"{_PUBLIC_SITE_KEY_PREFIX}{int(checked_at.timestamp()) // 900}"
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        existing = await db.scalar(
            select(OperationRun.id).where(
                OperationRun.hospital_id.is_(None),
                OperationRun.operation_type == _PUBLIC_SITE_OPERATION_TYPE,
                OperationRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return PublicSiteOutcome(False, False, False)
        run = OperationRun(
            hospital_id=None,
            operation_type=_PUBLIC_SITE_OPERATION_TYPE,
            state=(
                OperationRunState.FAILED.value if unavailable else OperationRunState.SUCCEEDED.value
            ),
            idempotency_key=idempotency_key,
            request_payload={},
            result_summary={"failing_hospitals": failing_hospitals},
            safe_error_code=PUBLIC_SITE_INCIDENT_TYPE if unavailable else None,
            safe_error_message=_public_site_cause(failing_hospitals) if unavailable else None,
            requested_at=checked_at,
            started_at=checked_at,
            completed_at=checked_at,
            total_count=1,
            success_count=0 if unavailable else 1,
            failure_count=1 if unavailable else 0,
        )
        db.add(run)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return PublicSiteOutcome(False, False, False)

        state = OperationRunState.FAILED if unavailable else OperationRunState.SUCCEEDED
        needed = _FAILURE_CHECKS if unavailable else _RECOVERY_CHECKS
        streak = await _state_streak(
            db,
            hospital_id=None,
            operation_type=_PUBLIC_SITE_OPERATION_TYPE,
            key_prefix=_PUBLIC_SITE_KEY_PREFIX,
            state=state.value,
            limit=needed,
        )
        opened = recovered = False
        if unavailable and streak >= needed:
            opened = await _open_public_site_incident(db, run, failing_hospitals)
        elif not unavailable and streak >= needed:
            recovered = await _recover_open_incident(
                db,
                hospital_id=None,
                source_type=_PUBLIC_SITE_SOURCE_TYPE,
                source_id=_PUBLIC_SITE_SOURCE_ID,
                run=run,
                reason="three consecutive monitor runs without shared 5xx",
            )
        await db.commit()
        return PublicSiteOutcome(True, opened, recovered)


def _public_site_cause(failing_hospitals: int) -> str:
    return (
        f"같은 점검에서 병원 {failing_hospitals}곳의 공개 주소가 서버 오류(HTTP 5xx)를 반환했습니다. "
        "병원별 DNS 문제가 아니라 공유 공개 서비스의 응답 문제입니다."
    )


async def _open_public_site_incident(
    db: AsyncSession, run: OperationRun, failing_hospitals: int
) -> bool:
    dedupe_key = build_incident_key(
        "domain_health",
        "public_site",
        _PUBLIC_SITE_SOURCE_ID,
        IncidentFingerprint.DOMAIN_UNHEALTHY,
    )
    previous = await db.scalar(select(Incident).where(Incident.dedupe_key == dedupe_key))
    # 병원 도메인과 같은 episode 규칙: 사람이 확인한 episode는 되살리지 않고, 복구 뒤 재발만 다시 알린다.
    if previous is not None and previous.state == IncidentState.ACKNOWLEDGED.value:
        return False
    should_notify = previous is None or previous.state == IncidentState.RECOVERED.value
    incident = await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="domain_health",
            object_type="public_site",
            object_id=_PUBLIC_SITE_SOURCE_ID,
            fingerprint=IncidentFingerprint.DOMAIN_UNHEALTHY,
            incident_type=PUBLIC_SITE_INCIDENT_TYPE,
            severity=IncidentSeverity.HIGH,
            customer_impact="여러 병원의 공개 페이지가 열리지 않아 환자와 AI가 콘텐츠를 읽지 못할 수 있습니다.",
            source_type=_PUBLIC_SITE_SOURCE_TYPE,
            next_action=(
                "개발 담당자는 공개 사이트 서비스와 로드밸런서 상태를 확인해 주세요. "
                "병원별 DNS는 변경하지 마세요."
            ),
            admin_path="/operations",
            hospital_id=None,
            operation_run_id=run.id,
            source_id=_PUBLIC_SITE_SOURCE_ID,
            safe_error_code=PUBLIC_SITE_INCIDENT_TYPE,
            safe_error_message=_public_site_cause(failing_hospitals),
        ),
        actor="domain-health-worker",
        reason="shared public service returned 5xx for several tenants",
        now=run.completed_at,
    )
    if should_notify:
        await enqueue_notification(
            db,
            build_open_incident_notification(
                incident_projection(incident, "공개 사이트 전체", run.id, "확인 필요"),
                settings.ADMIN_BASE_URL,
            ),
        )
    return should_notify
