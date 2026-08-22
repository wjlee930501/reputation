"""Refresh a hospital's stored domain status from an actual live observation.

The domain badges on the hospital list, the detail header and the profile
tracker all read `domain_cert_*`.  Those columns describe the certificate job,
not the domain: `PATCH /domain` clears them whenever the domain or DNS strategy
is re-saved, and nothing rewrites them for a domain that is already answering.
A hospital whose custom domain serves HTTPS with a valid certificate and the
correct tenant marker therefore kept reading "저장됨 · DNS 미확인" while the
onboarding checklist called the same domain complete.

A tenant-marker response is strictly stronger evidence than those columns: it
only succeeds when DNS resolves to the platform, TLS validates for the domain
and the platform routes it to this hospital.  So a successful live check is
allowed to promote the stored state, and every check — success or failure —
records when it happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models.hospital import DomainCertJobState

MAX_REASON_LENGTH = 100


@dataclass(frozen=True, slots=True)
class LiveDomainCheck:
    """One observation of a custom domain made against the public internet.

    ``proves_certificate`` separates the two kinds of evidence. A CNAME lookup
    only shows where the name points; an HTTPS tenant-marker response also shows
    that TLS validated for the domain, so only the latter may declare the
    certificate ready.
    """

    domain: str
    healthy: bool
    reason: str
    checked_at: datetime
    proves_certificate: bool = False


def normalize_domain(value: str | None) -> str:
    return (value or "").strip().lower().rstrip(".")


def apply_live_domain_check(hospital: Any, check: LiveDomainCheck) -> bool:
    """Write the observation onto the hospital row. Returns True when anything changed.

    The check is ignored when it names a different domain than the one currently
    connected — a result for the previous domain must not describe the new one.
    """
    domain = normalize_domain(check.domain)
    if not domain or normalize_domain(getattr(hospital, "aeo_domain", None)) != domain:
        return False

    checked_at = check.checked_at or datetime.now(UTC)
    hospital.domain_last_checked_at = checked_at
    hospital.domain_last_check_ok = check.healthy
    hospital.domain_last_check_reason = (check.reason or "")[:MAX_REASON_LENGTH] or None

    if not check.healthy:
        # 실패는 저장된 상태를 되돌리지 않는다. 한 번의 타임아웃으로 DNS 검증 이력을
        # 지우면 운영자가 이미 끝낸 작업을 다시 하게 된다.
        return True

    if getattr(hospital, "domain_cert_dns_verified_at", None) is None:
        hospital.domain_cert_dns_verified_at = checked_at

    if not check.proves_certificate:
        return True

    cert_state = getattr(hospital, "domain_cert_job_state", None)
    if cert_state == DomainCertJobState.ISSUING.value:
        # 발급 워커가 리스를 들고 있다. 여기서 DONE으로 덮으면 워커의 종료 갱신이
        # 조건 불일치로 무음 실패하고, 상태가 다시 뒤집힌다.
        return True

    if cert_state != DomainCertJobState.DONE.value:
        hospital.domain_cert_job_state = DomainCertJobState.DONE.value
        hospital.domain_cert_job_domain = domain
        hospital.domain_cert_job_token = None
        if getattr(hospital, "domain_cert_job_started_at", None) is None:
            hospital.domain_cert_job_started_at = checked_at
    return True
