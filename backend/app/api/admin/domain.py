import asyncio
import ipaddress
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.hospital import DomainCertJobState, DomainDnsStrategy, Hospital, HospitalStatus
from app.services.audit_log import default_actor, write_audit_log
from app.services.domain_certificate_manager import (
    DomainCertificateResult,
    ensure_domain_certificate,
)
from app.services.hospital_lifecycle import (
    activation_gate_error,
    evaluate_activation_gate,
)
from app.services.service_intervals import ServiceIntervalProvenance, open_service_interval

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Domain"])


class DomainVerifyResponse(BaseModel):
    domain: str
    verified: bool
    dns_verified: bool
    certificate_ready: bool
    certificate_phase: str | None = None
    cert_job_state: str | None = None
    cert_job_started_at: datetime | None = None
    cert_job_elapsed_minutes: int | None = None
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


@router.get("/{hospital_id}/domain/cert-status", response_model=DomainVerifyResponse)
async def check_cert_status(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """인증서 작업 상태 확인 전용 (멱등, 새 발급 트리거 안 함).
    
    ISSUING 상태를 확인하고 DONE/FAILED로 전환 가능. 새 provision 시작하지 않음.
    """
    hospital = await _get_hospital_or_404(db, hospital_id)

    if not hospital.aeo_domain:
        raise HTTPException(
            status_code=400, detail="도메인이 설정되지 않았습니다."
        )

    domain = hospital.aeo_domain
    cert_job_state = getattr(hospital, "domain_cert_job_state", None)
    cert_job_started_at = getattr(hospital, "domain_cert_job_started_at", None)
    dns_verified_at = getattr(hospital, "domain_cert_dns_verified_at", None)
    now = datetime.now(UTC)
    
    # DNS 미검증 상태
    if not dns_verified_at:
        return DomainVerifyResponse(
            domain=domain,
            verified=False,
            dns_verified=False,
            certificate_ready=False,
            certificate_phase=None,
            cert_job_state=cert_job_state,
            cert_job_started_at=cert_job_started_at,
            cert_job_elapsed_minutes=None,
            cname_value=None,
            expected_cname=settings.CNAME_TARGET,
            address_values=[],
            expected_addresses=[],
            verification_method=None,
            message="DNS 검증이 완료되지 않았습니다.",
        )
    
    # ISSUING 상태 → 실제 인증서 확인하여 DONE/FAILED로 전환
    if cert_job_state == DomainCertJobState.ISSUING.value:
        certificate = await asyncio.to_thread(
            lambda: __import__("app.services.domain_certificate_manager", fromlist=["inspect_domain_certificate"]).inspect_domain_certificate(domain)
        )
        
        if certificate and certificate.ready:
            # ISSUING → DONE
            hospital.domain_cert_job_state = DomainCertJobState.DONE.value
            cert_job_state = DomainCertJobState.DONE.value
        elif certificate and certificate.phase == "FAILED":
            # ISSUING → FAILED
            hospital.domain_cert_job_state = DomainCertJobState.FAILED.value
            cert_job_state = DomainCertJobState.FAILED.value
        elif cert_job_started_at and (now - cert_job_started_at).total_seconds() > 600:
            # 10분 타임아웃 → FAILED
            hospital.domain_cert_job_state = DomainCertJobState.FAILED.value
            cert_job_state = DomainCertJobState.FAILED.value
        
        await db.commit()
    
    elapsed_minutes = None
    if cert_job_started_at:
        elapsed_minutes = int((now - cert_job_started_at).total_seconds() / 60)
    
    certificate_ready = (cert_job_state == DomainCertJobState.DONE.value)
    
    if certificate_ready:
        message = f"DNS 확인 완료 · HTTPS 인증서 준비 완료"
    elif cert_job_state == DomainCertJobState.ISSUING.value:
        message = f"DNS 확인 완료 · HTTPS 인증서 발급 진행 중 (경과 {elapsed_minutes or 0}분)"
    elif cert_job_state == DomainCertJobState.FAILED.value:
        message = f"DNS 확인 완료 · HTTPS 인증서 발급 실패. 재시도가 필요합니다."
    else:
        message = f"DNS 확인 완료"
    
    return DomainVerifyResponse(
        domain=domain,
        verified=True,
        dns_verified=True,
        certificate_ready=certificate_ready,
        certificate_phase=cert_job_state,
        cert_job_state=cert_job_state,
        cert_job_started_at=cert_job_started_at,
        cert_job_elapsed_minutes=elapsed_minutes,
        cname_value=None,
        expected_cname=settings.CNAME_TARGET,
        address_values=[],
        expected_addresses=[],
        verification_method="status_check",
        message=message,
    )


@router.post("/{hospital_id}/domain/verify", response_model=DomainVerifyResponse)
async def verify_domain(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """DNS 검증 전용 엔드포인트. DNS 확인 성공 시 온보딩 5단계 완료.
    
    인증서 발급은 별도의 비동기 작업으로 분리되어 DNS 검증 완료 후에도 
    온보딩을 막지 않는다 (DM-F4). 인증서 발급 상태는 cert_job_* 필드로 추적한다 (DM-F1).
    """
    hospital = await _get_hospital_or_404(db, hospital_id)

    if not hospital.aeo_domain:
        raise HTTPException(
            status_code=400, detail="도메인이 설정되지 않았습니다. 먼저 도메인을 입력해 주세요."
        )

    domain = hospital.aeo_domain
    dns_strategy = domain_dns_strategy_for_hospital(hospital)
    dns_check = await check_domain_dns(domain, dns_strategy)

    if not dns_check.verified:
        # DNS 검증 실패 - 인증서 작업 불가
        message = _failure_message(domain, dns_strategy, dns_check)
        return DomainVerifyResponse(
            domain=domain,
            verified=False,
            dns_verified=False,
            certificate_ready=False,
            certificate_phase=None,
            cert_job_state=None,
            cert_job_started_at=None,
            cert_job_elapsed_minutes=None,
            cname_value=dns_check.cname_value,
            expected_cname=dns_check.expected_cname,
            address_values=dns_check.address_values,
            expected_addresses=dns_check.expected_addresses,
            verification_method=dns_check.verification_method,
            message=message,
        )

    # DNS 검증 성공 - 활성화 게이트 확인
    gate = await evaluate_activation_gate(db, hospital)
    if not gate["ready"]:
        raise HTTPException(
            status_code=409,
            detail=activation_gate_error(gate),
        )

    # DNS 검증 성공 타임스탬프 기록 (온보딩 5단계 운영자 작업 완료 마커)
    now = datetime.now(UTC)
    if not getattr(hospital, "domain_cert_dns_verified_at", None):
        hospital.domain_cert_dns_verified_at = now

    # DM-F4: DNS 검증 성공하면 즉시 site_live=True 전환 (인증서 기다리지 않음)
    previous_status = (
        hospital.status.value if hasattr(hospital.status, "value") else str(hospital.status)
    )
    previous_site_live = bool(hospital.site_live)
    
    if not hospital.site_live:
        hospital.site_live = True
        hospital.status = HospitalStatus.ACTIVE
        await open_service_interval(db, hospital.id, ServiceIntervalProvenance.ACTIVATION)

    # 인증서 작업 상태 확인 및 시작
    cert_job_state = getattr(hospital, "domain_cert_job_state", None)
    cert_job_started_at = getattr(hospital, "domain_cert_job_started_at", None)
    
    # DM-F2: 인증서 발급 작업이 이미 진행 중이면 409 반환 (멱등성)
    if cert_job_state == DomainCertJobState.ISSUING.value:
        elapsed_minutes = None
        if cert_job_started_at:
            elapsed_minutes = int((now - cert_job_started_at).total_seconds() / 60)
        
        await write_audit_log(
            db,
            action="verify_domain",
            hospital_id=hospital.id,
            actor=default_actor(),
            target_type="domain",
            target_id=domain,
            detail={
                "dns_verified": True,
                "cert_job_already_running": True,
                "cert_job_elapsed_minutes": elapsed_minutes,
            },
        )
        await db.commit()
        
        raise HTTPException(
            status_code=409,
            detail=f"HTTPS 인증서 발급이 이미 진행 중입니다 (경과 {elapsed_minutes or 0}분). 작업이 완료될 때까지 기다려 주세요.",
        )

    # 인증서 상태 확인 (이미 DONE이면 재발급 불필요)
    if cert_job_state != DomainCertJobState.DONE.value:
        # 인증서 발급 시작
        certificate = await ensure_verified_domain_certificate(domain)
        
        if certificate and certificate.ready:
            # 인증서 즉시 준비 완료
            hospital.domain_cert_job_state = DomainCertJobState.DONE.value
            hospital.domain_cert_job_started_at = now
            cert_job_state = DomainCertJobState.DONE.value
            cert_job_started_at = now
            
            await write_audit_log(
                db,
                action="provision_domain_certificate",
                hospital_id=hospital.id,
                actor=default_actor(),
                target_type="domain",
                target_id=domain,
                detail={
                    "dns_verified": True,
                    "certificate_ready": True,
                    "certificate_phase": certificate.phase,
                    "instant_ready": True,
                },
            )
        else:
            # 인증서 발급 진행 중
            hospital.domain_cert_job_state = DomainCertJobState.ISSUING.value
            hospital.domain_cert_job_started_at = now
            cert_job_state = DomainCertJobState.ISSUING.value
            cert_job_started_at = now
            
            await write_audit_log(
                db,
                action="provision_domain_certificate",
                hospital_id=hospital.id,
                actor=default_actor(),
                target_type="domain",
                target_id=domain,
                detail={
                    "dns_verified": True,
                    "certificate_ready": False,
                    "certificate_phase": certificate.phase if certificate else None,
                    "certificate_error_code": certificate.error_code if certificate else None,
                },
            )

    # DNS 검증 완료 감사 로그 (site_live 전환 포함)
    if not previous_site_live:
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
                "activation_gate": gate,
            },
        )
    
    await db.commit()

    # 응답 생성
    elapsed_minutes = None
    if cert_job_started_at:
        elapsed_minutes = int((now - cert_job_started_at).total_seconds() / 60)
    
    certificate_ready = (cert_job_state == DomainCertJobState.DONE.value)
    resolved_value = dns_check.cname_value or ", ".join(dns_check.address_values)
    
    if certificate_ready:
        message = f"DNS 확인 완료 · HTTPS 인증서 준비 완료 ({domain} → {resolved_value})"
    elif cert_job_state == DomainCertJobState.ISSUING.value:
        message = f"DNS 확인 완료 · HTTPS 인증서 발급 진행 중 (경과 {elapsed_minutes or 0}분). 일반적으로 수 분 내에 완료됩니다."
    else:
        message = f"DNS 확인 완료 ({domain} → {resolved_value}). 운영 전환은 완료되었으며, HTTPS 인증서는 백그라운드에서 발급됩니다."

    return DomainVerifyResponse(
        domain=domain,
        verified=True,
        dns_verified=True,
        certificate_ready=certificate_ready,
        certificate_phase=cert_job_state,
        cert_job_state=cert_job_state,
        cert_job_started_at=cert_job_started_at,
        cert_job_elapsed_minutes=elapsed_minutes,
        cname_value=dns_check.cname_value,
        expected_cname=dns_check.expected_cname,
        address_values=dns_check.address_values,
        expected_addresses=dns_check.expected_addresses,
        verification_method=dns_check.verification_method,
        message=message,
    )


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def ensure_verified_domain_certificate(
    domain: str,
) -> DomainCertificateResult | None:
    """DNS 확인 뒤 인증서/Map을 보장한다.

    로컬·테스트에서는 명시적으로 비활성화할 수 있다. 프로덕션 설정은 부팅 시
    ``CERTIFICATE_MANAGER_AUTO_PROVISION=true``를 강제하므로 운영에서 이 경로가
    우회되지는 않는다.
    """
    if not settings.CERTIFICATE_MANAGER_AUTO_PROVISION:
        return None
    return await asyncio.to_thread(ensure_domain_certificate, domain)


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
            return (
                f"DNS 검증 실패. CNAME 레코드를 추가해 주세요: {domain} → {settings.CNAME_TARGET}"
            )
        case DomainDnsStrategy.APEX_ADDRESS:
            target = (
                ", ".join(dns_check.expected_addresses) or "운영자가 설정한 글로벌 로드밸런서 IP"
            )
            return f"DNS 검증 실패. A/AAAA 레코드를 설정해 주세요: {domain} → {target}"


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h
