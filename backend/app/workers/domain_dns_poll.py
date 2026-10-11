"""PENDING_DOMAIN 병원의 자기 도메인 DNS를 주기적으로 확인해 수동 확인과 같은 경로로 연다.

판정·잠금·활성화·감사·인증서 작업은 전부 `verify_domain_for_hospital`이 한다. 여기는
대상 고르기, 창(기본 14일) 관리, 만료 사고 하나만 맡는다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException

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
    for target in targets:
        if poll_window_expired(await poll_started_at(target), now, max_days=max_days):
            await escalate(target)
            summary.expired += 1
            continue
        try:
            response = await verify(target)
        except HTTPException:
            # 게이트 미충족·도메인 변경·인증서 진행 중 — 사람 경로가 409로 답하는 상태다.
            summary.skipped += 1
            continue
        except Exception as exc:  # noqa: BLE001 — 한 도메인의 오류가 나머지를 막지 않는다.
            logger.warning("pending domain poll failed: code=%s", exc.__class__.__name__)
            summary.skipped += 1
            continue
        if getattr(response, "dns_verified", False):
            summary.verified += 1
            await recover(target)
        else:
            summary.not_ready += 1
    return summary
