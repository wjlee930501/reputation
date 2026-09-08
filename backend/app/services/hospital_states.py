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

from app.models.hospital import HospitalStatus


@dataclass(frozen=True, slots=True)
class PublicServiceState:
    kind: Literal["live", "paused", "not_live"]
    remaining: tuple[str, ...]  # 사람이 채울 조건 키: profile_complete · site_built


@dataclass(frozen=True, slots=True)
class ContentState:
    kind: Literal["auto", "preparing", "exception"]
    remaining: tuple[str, ...]  # schedule · sources:N · essence_review


@dataclass(frozen=True, slots=True)
class DomainState:
    kind: Literal["connected", "checking", "problem", "unused"]
    reason: str | None = None
    last_checked_at: Any = None


def public_service_state(hospital: Any) -> PublicServiceState:
    """`_has_public_site`(hospitals.py)와 같은 게이트. PAUSED는 남은 조건이 아니라 상태다."""
    status = getattr(hospital, "status", None)
    status = getattr(status, "value", status)
    if status == HospitalStatus.PAUSED.value:
        return PublicServiceState("paused", ())
    if status == HospitalStatus.ACTIVE.value and bool(getattr(hospital, "site_live", False)):
        return PublicServiceState("live", ())
    remaining = tuple(
        key for key in ("profile_complete", "site_built") if not bool(getattr(hospital, key, False))
    )
    return PublicServiceState("not_live", remaining)


def content_state(
    hospital: Any,
    *,
    essence_current: bool,
    unprocessed_sources: int,
    escalated_draft: bool,
) -> ContentState:
    """`schedule_set && essence_readiness.current` (설계 §4.2). 예외는 준비 중보다 앞선다."""
    if escalated_draft:
        return ContentState("exception", ())
    remaining: list[str] = []
    if not bool(getattr(hospital, "schedule_set", False)):
        remaining.append("schedule")
    if unprocessed_sources > 0:
        remaining.append(f"sources:{unprocessed_sources}")
    if not essence_current:
        remaining.append("essence_review")
    return ContentState("auto" if not remaining else "preparing", tuple(remaining))


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
    if job == "ISSUING":
        return DomainState("checking", last_checked_at=checked_at)
    if job == "FAILED":
        return DomainState("problem", reason="인증서 발급 실패", last_checked_at=checked_at)
    if job == "DONE":
        return DomainState("connected", last_checked_at=checked_at)
    check_ok = getattr(hospital, "domain_last_check_ok", None)
    if check_ok is True:
        return DomainState("connected", last_checked_at=checked_at)
    if check_ok is False:
        return DomainState(
            "problem",
            reason=getattr(hospital, "domain_last_check_reason", None) or "DNS 확인 실패",
            last_checked_at=checked_at,
        )
    return DomainState("checking", last_checked_at=checked_at)
