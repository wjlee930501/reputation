"""병원 3상태 — 공개 서비스 · 콘텐츠 준비 · 자기 도메인 (설계 §4.2).

admin 목록·헤더·현황이 전부 이 값을 받아 라벨만 붙인다. 판정 규칙을 admin에 두면
화면마다 갈라진다(PR-0A H-06의 재발).

콘텐츠 상태에 필요한 병원별 근거(`essence_current`·`unprocessed_sources`·
`escalated_draft`)는 `essence_readiness.get_essence_readiness_states`가 묶음으로 읽는다 —
여기서는 조회하지 않고 판정만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.models.hospital import Hospital, HospitalStatus
from app.services.hospital_lifecycle import (
    article_publication_verdict,
    profile_publication_verdict,
)


_KST = ZoneInfo("Asia/Seoul")


def contract_ended(hospital: Hospital, *, now: datetime | None = None) -> bool:
    """일시정지 병원 중 계약 종료일(KST)이 지난 병원 — Admin이 「운영 종료」로 묶는다.

    3상태에 네 번째 상태를 더하지 않는다: 운영 종료는 일시정지의 하위 묶음이고, 공개·발행·
    서비스 구간·보고서 대상은 일시정지와 같다(2026-11 개편 결정 대기 1번 B). 종료일이 있어도
    일시정지가 아니면(재개·운영 중) 운영 종료가 아니다. 오늘은 브라우저가 아니라 여기서 KST로 센다.
    """
    ended_on: date | None = getattr(hospital, "contract_ended_on", None)
    if ended_on is None or hospital.status != HospitalStatus.PAUSED:
        return False
    today = (now or datetime.now(_KST)).astimezone(_KST).date()
    return ended_on <= today


@dataclass(frozen=True, slots=True)
class PublicServiceState:
    kind: Literal["live", "paused", "not_live"]
    remaining: tuple[str, ...]  # 최소 공개 사실·서비스 권한 blocker key


@dataclass(frozen=True, slots=True)
class ContentState:
    kind: Literal["auto", "preparing", "exception"]
    # schedule · sources:N · sources_required · essence_review · service_paused · public_service
    remaining: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DomainState:
    kind: Literal["connected", "checking", "problem", "unused"]
    reason: str | None = None
    last_checked_at: Any = None
    # 마지막 관측의 성패. 화면이 "마지막 확인 …"에 응답 정상/실패를 붙이는 근거다.
    last_check_ok: bool | None = None


@dataclass(frozen=True, slots=True)
class AvailabilityBlocker:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class AvailabilityVerdict:
    available: bool
    blockers: tuple[AvailabilityBlocker, ...]


def schedule_availability_verdict(
    *,
    essence_current: bool,
    required_sources: int,
    unprocessed_sources: int,
    approved_philosophy_exists: bool,
) -> AvailabilityVerdict:
    """Return the backend-owned eligibility to create or replace a schedule."""

    if essence_current:
        return AvailabilityVerdict(available=True, blockers=())
    blockers: list[AvailabilityBlocker] = []
    if required_sources == 0:
        blockers.append(AvailabilityBlocker(
            "sources_required",
            "병원 정보 화면에서 근거 자료를 1개 이상 올려 주세요. 처리는 자동으로 이어집니다.",
        ))
    if unprocessed_sources > 0:
        blockers.append(AvailabilityBlocker(
            "sources_processing",
            f"근거 자료 처리 중 {unprocessed_sources}건입니다. 처리가 끝나면 자동으로 이어집니다.",
        ))
    blockers.append(AvailabilityBlocker(
        "essence_missing" if not approved_philosophy_exists else "essence_reapproval",
        "콘텐츠 운영 기준을 자동으로 만드는 중입니다."
        if not approved_philosophy_exists
        else "변경된 근거를 콘텐츠 운영 기준에 반영하는 중입니다.",
    ))
    return AvailabilityVerdict(available=False, blockers=tuple(blockers))


def generation_availability_verdict(
    hospital: Hospital,
    *,
    schedule_availability: AvailabilityVerdict,
) -> AvailabilityVerdict:
    """Return automatic-generation eligibility, separate from historical serving."""

    blockers: list[AvailabilityBlocker] = []
    if not bool(hospital.schedule_set):
        blockers.append(AvailabilityBlocker("schedule", "콘텐츠 발행 일정이 필요합니다."))
    blockers.extend(schedule_availability.blockers)
    blockers.extend(
        AvailabilityBlocker(code, "공개 서비스 상태를 확인해 주세요.")
        for code in article_publication_verdict(hospital).blockers
    )
    return AvailabilityVerdict(available=not blockers, blockers=tuple(blockers))


def serialize_availability(verdict: AvailabilityVerdict) -> dict[str, bool | list[dict[str, str]]]:
    return {
        "available": verdict.available,
        "blockers": [
            {"code": blocker.code, "message": blocker.message} for blocker in verdict.blockers
        ],
    }


#: 자동 공개를 기다리는 병원의 서비스·공개 권한 막힘을 대신하는 키(2026-11-20 어드민 개편).
#: `service_inactive`·`public_permission_missing`을 그대로 내면 사람 몫으로 보여, 다른 조건만
#: 채우면 시스템이 스스로 공개할 병원에 「서비스 활성화」·「공개 노출 확인」이 할 일로 떴다.
AUTO_ACTIVATION_PENDING = "auto_activation_pending"
#: 자기 도메인이 있는 병원은 DNS가 맞아야 공개된다 — 15분 감시가 확인하지만 DNS는 사람이 맞춘다.
CUSTOM_DOMAIN_PENDING = "custom_domain_pending"
#: 운영 중(ACTIVE)인데 공개 권한이 꺼진 병원 — 시스템이 되살리지 않는 명시적 비공개.
PUBLIC_PERMISSION_WITHDRAWN = "public_permission_withdrawn"

_SERVICE_BLOCKERS = frozenset({"service_inactive", "public_permission_missing"})


def _public_remaining(hospital: Any, status: Any, blockers: tuple[str, ...]) -> tuple[str, ...]:
    """공개 판정의 막힘 코드를 사람·시스템 몫이 드러나는 키로 바꾼다. 판정 자체는 그대로다."""
    from app.services.hospital_activation import AUTO_ACTIVATABLE_STATUSES, has_custom_domain

    auto_statuses = {item.value for item in AUTO_ACTIVATABLE_STATUSES}
    if status in auto_statuses:
        service_key = CUSTOM_DOMAIN_PENDING if has_custom_domain(hospital) else AUTO_ACTIVATION_PENDING
    elif status == HospitalStatus.ACTIVE.value:
        service_key = PUBLIC_PERMISSION_WITHDRAWN
    else:
        return blockers
    remaining: list[str] = []
    for code in blockers:
        key = service_key if code in _SERVICE_BLOCKERS else code
        if key not in remaining:
            remaining.append(key)
        # 자동 공개는 필수 병원 정보 완료(`profile_complete`)도 기다린다(`activation_requirements`).
        # 최소 공개 사실만 갖춘 병원이 시스템 몫 조건만 보이면 사람이 채울 일이 사라진다.
        if key == AUTO_ACTIVATION_PENDING and not bool(getattr(hospital, "profile_complete", False)):
            if "profile_complete" not in remaining:
                remaining.append("profile_complete")
    return tuple(remaining)


def public_service_state(hospital: Any) -> PublicServiceState:
    """Expose the canonical public-profile verdict. PAUSED remains a state."""
    status = getattr(hospital, "status", None)
    status = getattr(status, "value", status)
    if status == HospitalStatus.PAUSED.value:
        return PublicServiceState("paused", ())
    verdict = profile_publication_verdict(hospital)
    if verdict.allowed:
        return PublicServiceState("live", ())
    return PublicServiceState("not_live", _public_remaining(hospital, status, verdict.blockers))


def content_state(
    hospital: Any,
    *,
    essence_current: bool,
    unprocessed_sources: int,
    required_sources: int,
    escalated_draft: bool,
) -> ContentState:
    """`schedule_set && essence_readiness.current` (설계 §4.2). 예외는 준비 중보다 앞선다."""
    if escalated_draft and not essence_current:
        return ContentState("exception", ())
    status = getattr(hospital, "status", None)
    status = getattr(status, "value", status)
    schedule = schedule_availability_verdict(
        essence_current=essence_current,
        required_sources=required_sources,
        unprocessed_sources=unprocessed_sources,
        approved_philosophy_exists=required_sources > 0,
    )
    generation = generation_availability_verdict(
        hospital,
        schedule_availability=schedule,
    )
    if generation.available:
        return ContentState("auto", ())

    remaining: list[str] = []
    for blocker in generation.blockers:
        if blocker.code == "sources_processing":
            remaining.append(f"sources:{unprocessed_sources}")
        elif blocker.code in {"essence_missing", "essence_reapproval"}:
            remaining.append("essence_review" if required_sources > 0 else "sources_required")
        elif blocker.code == "service_inactive" and status == HospitalStatus.PAUSED.value:
            remaining.append("service_paused")
        elif blocker.code in {"service_inactive", "public_permission_missing", "site_not_built"}:
            if "public_service" not in remaining:
                remaining.append("public_service")
        else:
            remaining.append(blocker.code)
    return ContentState("preparing", tuple(dict.fromkeys(remaining)))


def domain_state(hospital: Any) -> DomainState:
    """자기 도메인이 있을 때만 의미가 있다. 순서는 admin `readHospitalDomainStatus`와 같다.

    진행 중·실패한 인증서 작업이 마지막 관측보다 앞선다 — 관측이 정상이어도 발급 지연을
    가리면 운영자가 손쓸 화면이 사라진다. 일시정지는 도메인 사실을 바꾸지 않는다(공개
    서비스 카드가 말한다).
    """
    if not getattr(hospital, "aeo_domain", None):
        return DomainState("unused")
    job = getattr(hospital, "domain_cert_job_state", None)
    job = getattr(job, "value", job)
    checked_at = getattr(hospital, "domain_last_checked_at", None)
    check_ok = getattr(hospital, "domain_last_check_ok", None)
    if job == "ISSUING":
        return DomainState("checking", last_checked_at=checked_at, last_check_ok=check_ok)
    if job == "FAILED":
        return DomainState(
            "problem",
            reason="인증서 발급 실패",
            last_checked_at=checked_at,
            last_check_ok=check_ok,
        )
    if job == "DONE" or check_ok is True:
        return DomainState("connected", last_checked_at=checked_at, last_check_ok=check_ok)
    if check_ok is False:
        return DomainState(
            "problem",
            reason=getattr(hospital, "domain_last_check_reason", None) or "DNS 확인 실패",
            last_checked_at=checked_at,
            last_check_ok=check_ok,
        )
    return DomainState("checking", last_checked_at=checked_at, last_check_ok=check_ok)
