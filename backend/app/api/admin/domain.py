import asyncio
import ipaddress
import socket
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.hospital import DomainDnsStrategy, Hospital, HospitalStatus
from app.services.audit_log import default_actor, write_audit_log

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Domain"])


class DomainVerifyResponse(BaseModel):
    domain: str
    verified: bool
    cname_value: str | None
    expected_cname: str
    address_values: list[str] = Field(default_factory=list)
    expected_addresses: list[str] = Field(default_factory=list)
    verification_method: str | None = None
    message: str


@dataclass(frozen=True)
class DomainDnsCheck:
    cname_value: str | None
    address_values: list[str]
    expected_cname: str
    expected_addresses: list[str]
    verified: bool
    verification_method: str | None


def missing_live_prerequisites(hospital: Hospital) -> list[str]:
    """LIVE(site_live/ACTIVE) 전환 전 충족돼야 하는 단계 — operations verify-domain과 공유.

    DNS만 맞으면 곧바로 라이브가 되어 V0/허브 빌드/스케줄을 건너뛰는 우회 경로를 막는다 (P1-6).
    """
    return [
        label
        for label, ready in (
            ("프로파일 완료", hospital.profile_complete),
            ("V0 리포트", hospital.v0_report_done),
            ("병원 정보 허브 빌드", hospital.site_built),
            ("콘텐츠 스케줄", hospital.schedule_set),
        )
        if not ready
    ]


@router.post("/{hospital_id}/domain/verify", response_model=DomainVerifyResponse)
async def verify_domain(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    hospital = await _get_hospital_or_404(db, hospital_id)

    if not hospital.aeo_domain:
        raise HTTPException(status_code=400, detail="도메인이 설정되지 않았습니다. 먼저 도메인을 입력해 주세요.")

    domain = hospital.aeo_domain
    dns_strategy = domain_dns_strategy_for_hospital(hospital)
    dns_check = await check_domain_dns(domain, dns_strategy)

    if dns_check.verified:
        # operations.py verify-domain / hospitals.py activate와 동일한 게이트 (P1-6).
        missing = missing_live_prerequisites(hospital)
        if missing:
            raise HTTPException(
                status_code=409,
                detail=f"도메인 DNS는 확인됐지만 LIVE 전환 전 단계가 남아 있습니다: {', '.join(missing)}",
            )
        previous_status = hospital.status.value if hasattr(hospital.status, "value") else str(hospital.status)
        previous_site_live = bool(hospital.site_live)
        hospital.site_live = True
        hospital.status = HospitalStatus.ACTIVE
        # operations.py verify-domain 경로와 동일하게 LIVE 전환을 감사 로그에 남긴다.
        await write_audit_log(
            db,
            action="verify_domain",
            hospital_id=hospital.id,
            actor=default_actor(),
            target_type="domain",
            target_id=domain,
            detail={
                "verified": True,
                "cname_value": dns_check.cname_value,
                "address_values": dns_check.address_values,
                "expected_cname": dns_check.expected_cname,
                "expected_addresses": dns_check.expected_addresses,
                "verification_method": dns_check.verification_method,
                "previous_status": previous_status,
                "previous_site_live": previous_site_live,
                "new_status": HospitalStatus.ACTIVE.value,
                "new_site_live": True,
            },
        )
        await db.commit()
        resolved_value = dns_check.cname_value or ", ".join(dns_check.address_values)
        message = f"공개 도메인 상태가 확인되었습니다. ({domain} → {resolved_value})"
    else:
        message = _failure_message(domain, dns_strategy, dns_check)

    return DomainVerifyResponse(
        domain=domain,
        verified=dns_check.verified,
        cname_value=dns_check.cname_value,
        expected_cname=dns_check.expected_cname,
        address_values=dns_check.address_values,
        expected_addresses=dns_check.expected_addresses,
        verification_method=dns_check.verification_method,
        message=message,
    )


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _resolve_cname(domain: str) -> str | None:
    return await asyncio.to_thread(_resolve_cname_blocking, domain)


def _resolve_cname_blocking(domain: str) -> str | None:
    try:
        # 더 정확한 CNAME 조회를 위해 dnspython이 있으면 사용, 없으면 socket fallback
        try:
            import dns.resolver

            answers = dns.resolver.resolve(domain, "CNAME", lifetime=5.0)
            return str(answers[0].target).rstrip(".")
        except ImportError:
            pass

        # fallback: getfqdn으로 최종 호스트 확인
        resolved = socket.getfqdn(domain)
        return resolved if resolved != domain else None
    except Exception:
        return None


async def _resolve_addresses(domain: str) -> list[str]:
    return await asyncio.to_thread(_resolve_addresses_blocking, domain)


def _resolve_addresses_blocking(domain: str) -> list[str]:
    try:
        try:
            import dns.resolver

            values: list[str] = []
            for record_type in ("A", "AAAA"):
                try:
                    answers = dns.resolver.resolve(domain, record_type, lifetime=5.0)
                except Exception:
                    continue
                values.extend(str(answer).rstrip(".") for answer in answers)
            return sorted(set(values))
        except ImportError:
            pass

        infos = socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP)
        return sorted({info[4][0] for info in infos})
    except Exception:
        return []


def _normalize_dns_name(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().rstrip(".").lower()


def _configured_custom_domain_ips() -> list[str]:
    values = []
    for raw in settings.CUSTOM_DOMAIN_IP_TARGETS.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        ipaddress.ip_address(candidate)
        values.append(candidate)
    return sorted(set(values))


async def check_domain_dns(
    domain: str,
    strategy: DomainDnsStrategy = DomainDnsStrategy.CNAME,
) -> DomainDnsCheck:
    cname_value, address_values = await asyncio.gather(
        _resolve_cname(domain),
        _resolve_addresses(domain),
    )
    expected_cname = settings.CNAME_TARGET
    expected_addresses = _configured_custom_domain_ips()
    cname_matches = _normalize_dns_name(cname_value) == _normalize_dns_name(expected_cname)
    address_matches = bool(set(address_values) & set(expected_addresses))
    match strategy:
        case DomainDnsStrategy.CNAME:
            verified = cname_matches
            verification_method = "cname" if cname_matches else None
        case DomainDnsStrategy.APEX_ADDRESS:
            verified = address_matches and cname_value is None
            verification_method = "address" if verified else None
    return DomainDnsCheck(
        cname_value=cname_value,
        address_values=address_values,
        expected_cname=expected_cname,
        expected_addresses=expected_addresses,
        verified=verified,
        verification_method=verification_method,
    )


def domain_dns_strategy_for_hospital(hospital: Hospital) -> DomainDnsStrategy:
    value = getattr(hospital, "domain_dns_strategy", DomainDnsStrategy.CNAME)
    if isinstance(value, str):
        try:
            return DomainDnsStrategy(value)
        except ValueError:
            return DomainDnsStrategy.CNAME
    return value


def _failure_message(domain: str, strategy: DomainDnsStrategy, dns_check: DomainDnsCheck) -> str:
    match strategy:
        case DomainDnsStrategy.CNAME:
            return f"DNS 검증 실패. CNAME 레코드를 추가해 주세요: {domain} → {settings.CNAME_TARGET}"
        case DomainDnsStrategy.APEX_ADDRESS:
            target = ", ".join(dns_check.expected_addresses) or "운영자가 설정한 글로벌 로드밸런서 IP"
            return f"DNS 검증 실패. A/AAAA 레코드를 설정해 주세요: {domain} → {target}"


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h
