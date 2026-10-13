"""PENDING_DOMAIN 병원의 자기 도메인 DNS를 주기적으로 확인해 수동 확인과 같은 경로로 연다.

판정·잠금·활성화·감사·인증서 작업은 전부 `verify_domain_for_hospital`이 한다. 여기는
대상 고르기와 창(기본 14일) 판정만 맡는다. 폴링은 창이 지난 뒤에도 계속하고, 창이 지났고
**이번 확인이 실제로 DNS 불일치를 관측했을 때만** 사고를 한 번 연다. DNS는 맞았는데 게이트 미충족·
도메인 변경·인증서 진행 중(409)이거나 브로커 장애(503)·예외였던 확인은 DNS 미설정의 근거가 아니다.

사고는 병원·도메인 쌍(`{hospital_id}:{domain}`)에 묶인다. 병원이 도메인을 바꾸면 옛 도메인의
사고는 원인이 사라진 것이므로 조용히 닫는다(`recover_settled_dns_incidents`).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.audit_log import reset_request_actor, set_request_actor
from app.services.domain_dns import normalize_dns_name
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint
from app.services.ops_incident_alerts import (
    open_ops_incident,
    recover_ops_incident,
    recover_ops_incident_by_id,
    recover_ops_incidents_for_hospital,
)

logger = logging.getLogger(__name__)

DOMAIN_DNS_POLL_ACTOR = "system:domain-dns-poll"
DOMAIN_DNS_POLL_PIPELINE = "domain_dns_poll"
DOMAIN_DNS_POLL_OBJECT_TYPE = "hospital_domain"
DOMAIN_DNS_INCIDENT_TYPE = "CUSTOM_DOMAIN_DNS_NOT_CONFIGURED"


@dataclass(frozen=True, slots=True)
class PendingDomain:
    hospital_id: uuid.UUID
    hospital_name: str
    domain: str


@dataclass(frozen=True, slots=True)
class ActiveDnsIncident:
    incident_id: uuid.UUID
    hospital_id: uuid.UUID
    hospital_name: str
    dedupe_key: str


@dataclass(slots=True)
class DomainPollSummary:
    verified: int = 0
    not_ready: int = 0
    skipped: int = 0
    expired: int = 0


def poll_window_expired(started_at: datetime | None, now: datetime, *, max_days: int) -> bool:
    return started_at is not None and now - started_at >= timedelta(days=max_days)


async def poll_pending_domains(
    targets: Sequence[PendingDomain],
    now: datetime,
    *,
    max_days: int,
    poll_started_at: Callable[[PendingDomain], Awaitable[datetime | None]],
    verify: Callable[[PendingDomain], Awaitable[object]],
    escalate: Callable[[PendingDomain], Awaitable[None]],
    recover: Callable[[PendingDomain], Awaitable[None]],
) -> DomainPollSummary:
    summary = DomainPollSummary()
    unknown_start = 0
    for target in targets:
        started_at = await poll_started_at(target)
        if started_at is None:
            unknown_start += 1
        expired = poll_window_expired(started_at, now, max_days=max_days)
        # 창이 지나도 계속 묻는다 — DNS 조회는 비용이 없고, 늦게 맞춘 병원도 같은 경로로 열린다.
        try:
            response = await verify(target)
        except HTTPException:
            # 게이트 미충족·도메인 변경·인증서 진행 중 — 사람 경로가 409로 답하는 상태다.
            summary.skipped += 1
            response = None
        except Exception as exc:  # noqa: BLE001 — 한 도메인의 오류가 나머지를 막지 않는다.
            logger.warning("pending domain poll failed: code=%s", exc.__class__.__name__)
            summary.skipped += 1
            response = None
        if getattr(response, "dns_verified", False):
            summary.verified += 1
            await recover(target)
            continue
        if response is None:
            # DNS 불일치를 관측하지 못한 확인(게이트·인프라)은 창이 지나도 사고의 근거가 아니다.
            continue
        summary.not_ready += 1
        if expired:
            # 사고는 한 번만 연다(이미 열려 있으면 `escalate`가 건드리지 않는다).
            await escalate(target)
            summary.expired += 1
    if unknown_start:
        logger.warning(
            "pending domain poll window start unknown: count=%s (not expired)", unknown_start
        )
    return summary


async def _load_targets_in(db: AsyncSession) -> list[PendingDomain]:
    rows = (
        await db.execute(
            select(Hospital.id, Hospital.name, Hospital.aeo_domain).where(
                Hospital.status == HospitalStatus.PENDING_DOMAIN,
                Hospital.aeo_domain.is_not(None),
            )
        )
    ).all()
    targets = []
    for hospital_id, name, domain in rows:
        normalized = normalize_dns_name(domain or "")
        if normalized:
            targets.append(PendingDomain(hospital_id, name, normalized))
    return targets


async def _poll_started_at_in(db: AsyncSession, target: PendingDomain) -> datetime | None:
    """창 시작 = 이 도메인을 마지막으로 저장한 시각(`connect_domain` 감사행).

    감사행이 없는 옛 병원은 병원 행의 마지막 수정 시각, 그것도 없으면 인증서 작업 시작
    시각을 쓴다. 셋 다 없으면 None(만료시키지 않는다).
    """

    saved_at = await db.scalar(
        select(func.max(AdminAuditLog.created_at)).where(
            AdminAuditLog.hospital_id == target.hospital_id,
            AdminAuditLog.action == "connect_domain",
            func.lower(AdminAuditLog.target_id) == target.domain,
        )
    )
    if saved_at is not None:
        return saved_at
    row = (
        await db.execute(
            select(Hospital.updated_at, Hospital.domain_cert_job_started_at).where(
                Hospital.id == target.hospital_id
            )
        )
    ).first()
    if row is None:
        return None
    return row[0] or row[1]


def _object_id(target: PendingDomain) -> str:
    return f"{target.hospital_id}:{target.domain}"


def _dedupe_key(target: PendingDomain) -> str:
    """열기(`open_ops_incident`)와 같은 함수로 만든 사고 키. 저장된 `Incident.dedupe_key`와 비교한다.

    저장된 `source_id`는 비교에 쓰지 않는다 — 점이 든 `{hospital_id}:{domain}`은 저장할 때
    `normalize_source_id`가 `sha256:…`로 바꾸므로 원래 쌍과 같아지지 않는다.
    """
    return build_incident_key(
        DOMAIN_DNS_POLL_PIPELINE, DOMAIN_DNS_POLL_OBJECT_TYPE, _object_id(target),
        IncidentFingerprint.MISSING_PREREQUISITE,
    )


def should_open_dns_incident(
    state: str | None, acknowledged_at: datetime | None, window_started_at: datetime | None
) -> bool:
    """창이 지난 DNS 불일치에 사고를 (다시) 열지 정한다.

    없거나 RECOVERED면 연다. 확인 처리(ACKNOWLEDGED)된 사고는 그 뒤에 도메인을 다시 저장해 새 창이
    시작된 경우에만 다시 연다(공통 `open_or_touch_incident`가 episode_seq+1로 연다) — 도메인을 바꿨다가
    (옛 쌍 정리가 닫음) 같은 도메인으로 되돌린 뒤 다시 14일이 지난 것은 새 실패다. 같은 창 안에서 사람이
    확인한 사고는 폴링마다 다시 열지 않는다. 열려 있는(OPEN·RETRYING) 사고는 건드리지 않는다.
    """
    if state is None or state == IncidentState.RECOVERED.value:
        return True
    if state != IncidentState.ACKNOWLEDGED.value:
        return False
    return (
        acknowledged_at is not None
        and window_started_at is not None
        and window_started_at > acknowledged_at
    )


async def _escalate(target: PendingDomain) -> None:
    async with get_async_sessionmaker()() as db:
        row = (
            await db.execute(
                select(Incident.state, Incident.acknowledged_at).where(
                    Incident.dedupe_key == _dedupe_key(target)
                )
            )
        ).first()
        state, acknowledged_at = row if row is not None else (None, None)
        window_started_at = (
            await _poll_started_at_in(db, target)
            if state == IncidentState.ACKNOWLEDGED.value
            else None
        )
    if not should_open_dns_incident(state, acknowledged_at, window_started_at):
        return
    await open_ops_incident(
        pipeline=DOMAIN_DNS_POLL_PIPELINE,
        object_type=DOMAIN_DNS_POLL_OBJECT_TYPE,
        object_id=_object_id(target),
        incident_type=DOMAIN_DNS_INCIDENT_TYPE,
        safe_error_code=DOMAIN_DNS_INCIDENT_TYPE,
        problem=(
            f"자기 도메인 {target.domain}의 DNS가 "
            f"{settings.DOMAIN_DNS_POLL_MAX_DAYS}일 동안 맞춰지지 않았습니다."
        ),
        customer_impact="공개 페이지와 콘텐츠 발행이 시작되지 않습니다.",
        next_action=(
            "병원 정보 화면의 공개 주소에서 DNS 안내를 병원에 다시 전달하거나, "
            "기본 주소로 운영할지 정해 주세요."
        ),
        source_type="DOMAIN_DNS_POLL",
        hospital_name=target.hospital_name,
        hospital_id=target.hospital_id,
        admin_path=f"/hospitals/{target.hospital_id}/info#domain-setup",
        fingerprint=IncidentFingerprint.MISSING_PREREQUISITE,
        actor=DOMAIN_DNS_POLL_ACTOR,
        severity=IncidentSeverity.MEDIUM,
    )


async def _recover(target: PendingDomain) -> None:
    await recover_ops_incident(
        pipeline=DOMAIN_DNS_POLL_PIPELINE,
        object_type=DOMAIN_DNS_POLL_OBJECT_TYPE,
        object_id=_object_id(target),
        fingerprint=IncidentFingerprint.MISSING_PREREQUISITE,
        hospital_name=target.hospital_name,
        actor=DOMAIN_DNS_POLL_ACTOR,
        reason="custom domain verified",
        notify=False,
    )


async def _verify(target: PendingDomain):
    from app.api.admin.domain import verify_domain_for_hospital

    token = set_request_actor(DOMAIN_DNS_POLL_ACTOR)
    try:
        async with get_async_sessionmaker()() as db:
            return await verify_domain_for_hospital(target.hospital_id, db)
    finally:
        reset_request_actor(token)


async def recover_settled_dns_incidents(
    targets: Sequence[PendingDomain],
    flagged: Sequence[ActiveDnsIncident],
    *,
    recover: Callable[[uuid.UUID, str], Awaitable[object]],
    recover_stale: Callable[[uuid.UUID, str], Awaitable[object]],
) -> int:
    """원인이 사라진 만료 사고를 닫는다. `flagged`는 열려 있는 DNS 사고(id·병원·병원명·사고 키)다.

    폴러는 창이 지난 뒤에도 계속 묻고 DNS가 맞으면 성공 경로로 사고를 닫는다. 그러나 사람이
    수동 확인으로 먼저 열었거나 상태가 바뀌어 대기 목록에서 빠진 병원은 폴러가 다시 묻지 않으므로
    그 경로로 닫히지 않는다 — 대기 목록에서 빠진 병원은 원인이 사라진 것이다. 아직 대기 중이어도
    도메인을 바꾼 병원의 옛 도메인 사고는 폴러가 다시 묻지 않는 쌍이라 같은 이유로 닫는다.
    """

    pending_ids = {target.hospital_id for target in targets}
    # 지금 대기 중인 병원·도메인 쌍의 사고 키 — 여는 경로와 같은 함수로 만든다.
    current_keys = {_dedupe_key(target) for target in targets}
    settled: dict[uuid.UUID, str] = {}
    stale: list[tuple[uuid.UUID, str]] = []
    for incident in flagged:
        if incident.hospital_id not in pending_ids:
            settled.setdefault(incident.hospital_id, incident.hospital_name)
        elif incident.dedupe_key not in current_keys:
            stale.append((incident.incident_id, incident.hospital_name))
    for hospital_id, name in settled.items():
        await recover(hospital_id, name)
    for incident_id, name in stale:
        await recover_stale(incident_id, name)
    return len(settled) + len(stale)


async def _active_dns_incidents(db: AsyncSession) -> list[ActiveDnsIncident]:
    rows = (
        await db.execute(
            select(Incident.id, Incident.hospital_id, Hospital.name, Incident.dedupe_key)
            .join(Hospital, Hospital.id == Incident.hospital_id)
            .where(
                Incident.incident_type == DOMAIN_DNS_INCIDENT_TYPE,
                Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value)),
            )
        )
    ).all()
    return [ActiveDnsIncident(*row) for row in rows]


async def _recover_hospital(hospital_id: uuid.UUID, hospital_name: str) -> None:
    await recover_ops_incidents_for_hospital(
        hospital_id=hospital_id,
        pipeline=DOMAIN_DNS_POLL_PIPELINE,
        incident_type=DOMAIN_DNS_INCIDENT_TYPE,
        hospital_name=hospital_name,
        actor=DOMAIN_DNS_POLL_ACTOR,
        reason="hospital is no longer waiting for its custom domain",
        notify=False,
    )


async def _recover_stale_incident(incident_id: uuid.UUID, hospital_name: str) -> None:
    # 옛 도메인의 키는 지금 도메인으로 다시 만들 수 없다 — 조회한 사고 id로 닫는다.
    await recover_ops_incident_by_id(
        incident_id,
        hospital_name=hospital_name,
        actor=DOMAIN_DNS_POLL_ACTOR,
        reason="hospital changed its custom domain",
        notify=False,
    )


async def poll_pending_custom_domains_once(now: datetime) -> DomainPollSummary:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        targets = await _load_targets_in(db)

    async def started_at(target: PendingDomain) -> datetime | None:
        async with sessions() as db:
            return await _poll_started_at_in(db, target)

    summary = await poll_pending_domains(
        targets,
        now,
        max_days=settings.DOMAIN_DNS_POLL_MAX_DAYS,
        poll_started_at=started_at,
        verify=_verify,
        escalate=_escalate,
        recover=_recover,
    )
    async with sessions() as db:
        flagged = await _active_dns_incidents(db)
    await recover_settled_dns_incidents(
        targets, flagged, recover=_recover_hospital, recover_stale=_recover_stale_incident
    )
    return summary
