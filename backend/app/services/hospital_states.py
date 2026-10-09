"""병원 3상태 — 공개 서비스 · 콘텐츠 준비 · 자기 도메인 (설계 §4.2).

admin 목록·헤더·현황이 전부 이 값을 받아 라벨만 붙인다. 판정 규칙을 admin에 두면
화면마다 갈라진다(PR-0A H-06의 재발).

콘텐츠 상태에 필요한 병원별 근거(`essence_current`·`unprocessed_sources`·
`escalated_draft`)는 `essence_readiness.get_essence_readiness_states`가 묶음으로 읽는다 —
여기서는 조회하지 않고 판정만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.models.hospital import Hospital, HospitalStatus
from app.services.hospital_lifecycle import (
    article_publication_verdict,
    profile_publication_verdict,
)


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


def public_service_state(hospital: Any) -> PublicServiceState:
    """Expose the canonical public-profile verdict. PAUSED remains a state."""
    status = getattr(hospital, "status", None)
    status = getattr(status, "value", status)
    if status == HospitalStatus.PAUSED.value:
        return PublicServiceState("paused", ())
    verdict = profile_publication_verdict(hospital)
    if verdict.allowed:
        return PublicServiceState("live", ())
    return PublicServiceState("not_live", verdict.blockers)


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
