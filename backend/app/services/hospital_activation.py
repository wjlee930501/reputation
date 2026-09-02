"""STEP 5 공개 활성화 전환 — Admin 엔드포인트와 워커가 공유하는 한 벌의 로직.

활성화 게이트 세 가지(`profile_complete`·`v0_report_done`·`site_built`)는 모두 시스템이
스스로 세우는 플래그다. 그런데도 마지막 전환만 사람 클릭이었고, 그 클릭을 재촉하는
Slack이 두 건 붙어 있었다. 기본 플랫폼 주소는 DNS도 인증서도 사람 손이 필요 없으므로,
게이트가 모두 통과하면 허브 준비 태스크가 그대로 운영을 시작한다.

자기 도메인(`aeo_domain`)이 지정된 병원만 수동 경로로 남는다 — DNS는 병원 것이고,
활성화 시점을 시스템이 정할 수 없다.

엔드포인트(async)와 Celery 워커(sync)는 세션 종류가 달라서 커밋 경로만 두 벌이고,
"활성화해도 되는가"와 "무엇을 바꾸는가"는 이 모듈 한 곳에서만 결정한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import HospitalServiceInterval
from app.services.audit_log import write_audit_log, write_audit_log_sync
from app.services.content_citations import platform_public_base_url
from app.services.domain_live_status import normalize_domain
from app.services.hospital_lifecycle import ActivationGateSnapshot, activation_gate_snapshot
from app.services.service_intervals import ServiceIntervalProvenance, open_service_interval

ACTIVATE_AUDIT_ACTION = "activate_hospital"
#: 사람이 누르지 않은 활성화의 감사 로그 actor. `SYSTEM_AUTO_PUBLISH`(자동 발행)와 같은 규약.
AUTO_ACTIVATE_ACTOR = "SYSTEM_AUTO_ACTIVATE"
PLATFORM_ACTIVATION_METHOD = "platform_subdomain"


class ActivationOutcome(StrEnum):
    """이 호출이 실제로 무엇을 했는지."""

    ACTIVATED = "ACTIVATED"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    BLOCKED = "BLOCKED"


class AutoActivationBlocker(StrEnum):
    """자동 활성화를 막은 이유 — Slack 문구와 테스트가 이 값을 읽는다."""

    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    GATES_NOT_MET = "GATES_NOT_MET"
    CUSTOM_DOMAIN_PENDING = "CUSTOM_DOMAIN_PENDING"
    STATUS_NOT_AUTO_ADVANCEABLE = "STATUS_NOT_AUTO_ADVANCEABLE"


#: 자동 전환을 허용하는 상태. PAUSED(운영자가 일부러 멈춘 병원)와 ACTIVE(이미 전환됨)는
#: 어떤 재실행에도 자동으로 되살리지 않는다.
AUTO_ACTIVATABLE_STATUSES: frozenset[HospitalStatus] = frozenset(
    {
        HospitalStatus.ONBOARDING,
        HospitalStatus.ANALYZING,
        HospitalStatus.BUILDING,
        HospitalStatus.PENDING_DOMAIN,
    }
)

_BLOCKER_REASONS: dict[AutoActivationBlocker, str] = {
    AutoActivationBlocker.ALREADY_ACTIVE: "이미 공개 운영 중입니다.",
    AutoActivationBlocker.GATES_NOT_MET: "공개 활성화 선행 조건이 아직 남아 있습니다.",
    AutoActivationBlocker.CUSTOM_DOMAIN_PENDING: (
        "병원 자기 도메인이 지정돼 있어 DNS·인증서 확인 후 직접 운영을 시작해야 합니다."
    ),
    AutoActivationBlocker.STATUS_NOT_AUTO_ADVANCEABLE: (
        "일시 정지 등 자동 전환 대상이 아닌 상태입니다."
    ),
}


def blocker_reason(blocker: AutoActivationBlocker) -> str:
    return _BLOCKER_REASONS[blocker]


#: `/activate`가 PAUSED 병원을 되살릴 때 쓰는 코드. 재개는 DNS 재확인이 붙은 `/resume`만
#: 수행한다 — 두 경로가 갈라지면 일시 정지가 우회된다.
STATUS_NOT_ACTIVATABLE_CODE = "STATUS_NOT_ACTIVATABLE"
STATUS_NOT_ACTIVATABLE_MESSAGE = (
    "일시 정지 등 자동 전환 대상이 아닌 상태입니다. 재개는 /resume 으로 진행해 주세요."
)


class HospitalNotActivatable(Exception):
    """자동 전환 대상이 아닌 상태에서 활성화를 시도했다.

    `AUTO_ACTIVATABLE_STATUSES` 판정을 `evaluate_auto_activation`에만 두면 워커 경로만
    보호되고 Admin `/activate`는 PAUSED를 그대로 ACTIVE로 되살린다. 전환 함수 자체에서
    막아 두 경로가 같은 규칙을 쓰게 한다.
    """

    def __init__(self, status: HospitalStatus) -> None:
        super().__init__(STATUS_NOT_ACTIVATABLE_MESSAGE)
        self.status = status
        self.code = STATUS_NOT_ACTIVATABLE_CODE
        self.message = STATUS_NOT_ACTIVATABLE_MESSAGE

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class ActivationResult:
    outcome: ActivationOutcome
    status: HospitalStatus
    site_live: bool
    gate: ActivationGateSnapshot

    @property
    def activated_now(self) -> bool:
        """이번 호출이 전환을 수행했는가 — 알림·revalidate는 이때만 한 번."""
        return self.outcome is ActivationOutcome.ACTIVATED


def has_custom_domain(hospital: Hospital) -> bool:
    """자기 도메인이 지정됐는가. 비어 있으면 기본 플랫폼 주소(관리 모드 기본값)로 본다."""
    return bool(normalize_domain(getattr(hospital, "aeo_domain", None)))


def public_site_url(aeo_domain: str | None, slug: str | None) -> str:
    """실제 접근 가능한 공개 허브 URL.

    site.py의 호스트 라우팅 규칙과 일치시킨다:
      1. 병원 자기 도메인(aeo_domain)이 있으면 https://{aeo_domain}/
      2. 없으면 기본 서브도메인 https://{slug}.{platform host}/  (SITE_BASE_URL 호스트 파생)

    `has_custom_domain`과 **같은** 정규화를 쓴다 — 공백뿐인 aeo_domain을 truthy로 보면
    자동 활성화는 기본 주소로 가는데 Slack 문구만 "https://   /"가 되어 어긋난다.
    """
    domain = normalize_domain(aeo_domain)
    if domain:
        return f"https://{domain}/"
    # 인용 귀속(content_citations)이 매칭에 쓰는 것과 같은 호스트 규칙을 재사용한다 —
    # 두 곳이 어긋나면 실제로 서빙되는 주소가 owned로 집계되지 않는다.
    return platform_public_base_url(slug) or settings.SITE_BASE_URL


def evaluate_auto_activation(hospital: Hospital) -> AutoActivationBlocker | None:
    """기본 주소 자동 활성화가 가능한지. 가능하면 None.

    순수 판정이라 워커·엔드포인트·테스트가 세션 없이 같은 답을 얻는다.
    """
    if hospital.status is HospitalStatus.ACTIVE:
        return AutoActivationBlocker.ALREADY_ACTIVE
    if hospital.status not in AUTO_ACTIVATABLE_STATUSES:
        return AutoActivationBlocker.STATUS_NOT_AUTO_ADVANCEABLE
    if has_custom_domain(hospital):
        return AutoActivationBlocker.CUSTOM_DOMAIN_PENDING
    if not activation_gate_snapshot(hospital)["ready"]:
        return AutoActivationBlocker.GATES_NOT_MET
    return None


def _already_active_result(hospital: Hospital, gate: ActivationGateSnapshot) -> ActivationResult:
    return ActivationResult(
        ActivationOutcome.ALREADY_ACTIVE,
        HospitalStatus.ACTIVE,
        bool(hospital.site_live),
        gate,
    )


def _apply_transition(hospital: Hospital) -> str:
    """행을 ACTIVE·site_live로 바꾸고 직전 상태 문자열을 돌려준다."""
    previous_status = (
        hospital.status.value if hasattr(hospital.status, "value") else str(hospital.status)
    )
    hospital.status = HospitalStatus.ACTIVE
    hospital.site_live = True
    return previous_status


def _audit_detail(
    hospital: Hospital,
    previous_status: str,
    gate: ActivationGateSnapshot,
    reason: str,
) -> dict:
    return {
        "previous_status": previous_status,
        "new_status": HospitalStatus.ACTIVE.value,
        "aeo_domain": hospital.aeo_domain,
        "activation_method": PLATFORM_ACTIVATION_METHOD,
        "certificate_ready": True,
        "activation_gate": gate,
        "reason": reason,
    }


async def activate_hospital(
    db: AsyncSession,
    hospital: Hospital,
    *,
    actor: str | None = None,
    reason: str,
) -> ActivationResult:
    """공개 운영 전환 한 번. 커밋은 호출자 책임이다 (감사행 → commit → 외부 효과).

    자동 전환 대상이 아닌 상태(PAUSED 등)는 `HospitalNotActivatable`로 막는다.
    """

    if hospital.status is HospitalStatus.ACTIVE:
        return _already_active_result(hospital, activation_gate_snapshot(hospital))
    if hospital.status not in AUTO_ACTIVATABLE_STATUSES:
        raise HospitalNotActivatable(hospital.status)

    gate = activation_gate_snapshot(hospital)
    if not gate["ready"]:
        return ActivationResult(
            ActivationOutcome.BLOCKED, hospital.status, bool(hospital.site_live), gate
        )

    previous_status = _apply_transition(hospital)
    await open_service_interval(db, hospital.id, ServiceIntervalProvenance.ACTIVATION)
    await write_audit_log(
        db,
        action=ACTIVATE_AUDIT_ACTION,
        hospital_id=hospital.id,
        actor=actor,
        target_type="hospital",
        target_id=hospital.id,
        detail=_audit_detail(hospital, previous_status, gate, reason),
    )
    return ActivationResult(ActivationOutcome.ACTIVATED, HospitalStatus.ACTIVE, True, gate)


def activate_hospital_sync(
    db: Session,
    hospital: Hospital,
    *,
    actor: str = AUTO_ACTIVATE_ACTOR,
    reason: str,
) -> ActivationResult:
    """워커용 동기 전환. async 경로와 같은 판정·같은 감사 행을 남긴다."""

    if hospital.status is HospitalStatus.ACTIVE:
        return _already_active_result(hospital, activation_gate_snapshot(hospital))
    if hospital.status not in AUTO_ACTIVATABLE_STATUSES:
        raise HospitalNotActivatable(hospital.status)

    gate = activation_gate_snapshot(hospital)
    if not gate["ready"]:
        return ActivationResult(
            ActivationOutcome.BLOCKED, hospital.status, bool(hospital.site_live), gate
        )

    previous_status = _apply_transition(hospital)
    _open_service_interval_sync(db, hospital.id)
    write_audit_log_sync(
        db,
        action=ACTIVATE_AUDIT_ACTION,
        hospital_id=hospital.id,
        actor=actor,
        target_type="hospital",
        target_id=hospital.id,
        detail=_audit_detail(hospital, previous_status, gate, reason),
    )
    return ActivationResult(ActivationOutcome.ACTIVATED, HospitalStatus.ACTIVE, True, gate)


def _open_service_interval_sync(db: Session, hospital_id: uuid.UUID) -> HospitalServiceInterval:
    """`open_service_interval`의 동기 판박이 — 재실행 시 열린 구간을 재사용한다."""

    db.execute(select(Hospital.id).where(Hospital.id == hospital_id).with_for_update())
    current = db.execute(
        select(HospitalServiceInterval)
        .where(
            HospitalServiceInterval.hospital_id == hospital_id,
            HospitalServiceInterval.ended_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if current is not None:
        return current
    interval = HospitalServiceInterval(
        hospital_id=hospital_id,
        started_at=datetime.now(UTC),
        provenance=ServiceIntervalProvenance.ACTIVATION.value,
    )
    db.add(interval)
    return interval
