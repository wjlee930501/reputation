import asyncio
import ipaddress
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.hospital import DomainDnsStrategy, DomainManagementMode, Hospital
from app.services.domain_certificate_manager import inspect_domain_certificate

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Domain Setup"])
DomainEnumT = TypeVar("DomainEnumT", DomainManagementMode, DomainDnsStrategy)


class DomainSetupRecord(BaseModel):
    type: str
    name: str
    host: str
    registrar_host: str | None = None
    value: str
    ttl: str = "300 (또는 등록기관 최소값)"
    purpose: str


class DomainSetupChecklistItem(BaseModel):
    key: str
    label: str
    description: str
    status: str


class DomainSetupResponse(BaseModel):
    domain: str | None
    management_mode: str
    dns_strategy: str
    domain_management_mode: str
    domain_dns_strategy: str
    registrar: str | None = None
    dns_provider: str | None = None
    purchase_note: str | None = None
    domain_registrar: str | None = None
    domain_dns_provider: str | None = None
    domain_purchase_note: str | None = None
    expected_cname: str
    expected_addresses: list[str] = Field(default_factory=list)
    certificate_ready: bool = False
    certificate_phase: str | None = None
    records: list[DomainSetupRecord] = Field(default_factory=list)
    checklist: list[DomainSetupChecklistItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class DomainSetupState:
    domain: str | None
    management_mode: DomainManagementMode
    dns_strategy: DomainDnsStrategy
    registrar: str | None
    dns_provider: str | None
    purchase_note: str | None
    site_live: bool
    dns_verified_at: datetime | None
    cert_job_state: str | None


@router.get("/{hospital_id}/domain/setup", response_model=DomainSetupResponse)
async def get_domain_setup(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")

    state = _domain_setup_state(hospital)
    certificate = None
    if state.domain and settings.CERTIFICATE_MANAGER_AUTO_PROVISION:
        certificate = await asyncio.to_thread(inspect_domain_certificate, state.domain)
    addresses = _configured_custom_domain_ips()
    records, warnings = _domain_records(state.domain, state.dns_strategy, addresses)
    return DomainSetupResponse(
        domain=state.domain,
        management_mode=state.management_mode.value,
        dns_strategy=state.dns_strategy.value,
        domain_management_mode=state.management_mode.value,
        domain_dns_strategy=state.dns_strategy.value,
        registrar=state.registrar,
        dns_provider=state.dns_provider,
        purchase_note=state.purchase_note,
        domain_registrar=state.registrar,
        domain_dns_provider=state.dns_provider,
        domain_purchase_note=state.purchase_note,
        expected_cname=settings.CNAME_TARGET,
        expected_addresses=addresses,
        certificate_ready=bool(certificate and certificate.ready),
        certificate_phase=certificate.phase if certificate else None,
        records=records,
        checklist=_checklist(state, certificate_ready=bool(certificate and certificate.ready)),
        warnings=warnings,
    )


def _domain_setup_state(hospital: Hospital) -> DomainSetupState:
    return DomainSetupState(
        domain=hospital.aeo_domain,
        management_mode=_enum_or_default(
            getattr(hospital, "domain_management_mode", None),
            DomainManagementMode.HOSPITAL_MANAGED,
        ),
        dns_strategy=_enum_or_default(
            getattr(hospital, "domain_dns_strategy", None),
            DomainDnsStrategy.CNAME,
        ),
        registrar=getattr(hospital, "domain_registrar", None),
        dns_provider=getattr(hospital, "domain_dns_provider", None),
        purchase_note=getattr(hospital, "domain_purchase_note", None),
        site_live=bool(getattr(hospital, "site_live", False)),
        dns_verified_at=getattr(hospital, "domain_cert_dns_verified_at", None),
        cert_job_state=getattr(hospital, "domain_cert_job_state", None),
    )


def _enum_or_default(value: DomainEnumT | str | None, default: DomainEnumT) -> DomainEnumT:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return type(default)(value)
        except ValueError:
            return default
    return value


def _configured_custom_domain_ips() -> list[str]:
    values: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for raw in settings.CUSTOM_DOMAIN_IP_TARGETS.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        values.append(ipaddress.ip_address(candidate))
    return [
        str(value) for value in sorted(set(values), key=lambda value: (value.version, str(value)))
    ]


def _domain_records(
    domain: str | None,
    strategy: DomainDnsStrategy,
    addresses: list[str],
) -> tuple[list[DomainSetupRecord], list[str]]:
    if not domain:
        return [], []
    match strategy:
        case DomainDnsStrategy.CNAME:
            # DM-U2: CNAME 대상값에 trailing dot 포함
            cname_with_dot = settings.CNAME_TARGET if settings.CNAME_TARGET.endswith(".") else f"{settings.CNAME_TARGET}."
            # DM-F5: 호스트 컬럼에 FQDN과 등록기관 호스트 표시
            registrar_host = domain.split('.')[0] if domain and '.' in domain else None
            warnings = [
                "FQDN 입력 시 끝에 점(.)을 붙이는 등록기관도 있습니다. 등록기관 UI 규칙을 확인하세요.",
                "TTL은 DNS 검증 속도에 영향을 주지 않습니다. 등록기관 최소값을 사용하세요.",
                "Gabia 사용 시: DNS 레코드 입력 후 반드시 '확인 후 저장' 버튼을 클릭해야 적용됩니다.",
            ]
            return [
                DomainSetupRecord(
                    type="CNAME",
                    name=domain,
                    host=domain,
                    registrar_host=registrar_host,
                    value=cname_with_dot,
                    purpose="병원 정보 허브 트래픽을 Reputation 플랫폼으로 연결",
                )
            ], warnings
        case DomainDnsStrategy.APEX_ADDRESS:
            # DM-F5: apex의 경우 registrar_host는 @ 또는 도메인 자체
            registrar_host = "@"
            records = [
                DomainSetupRecord(
                    type="AAAA" if ":" in address else "A",
                    name=domain,
                    host=domain,
                    registrar_host=registrar_host,
                    value=address,
                    purpose="루트 도메인을 Reputation 글로벌 로드밸런서로 연결",
                )
                for address in addresses
            ]
            base_warnings = [
                "FQDN 입력 시 끝에 점(.)을 붙이는 등록기관도 있습니다. 등록기관 UI 규칙을 확인하세요.",
                "TTL은 DNS 검증 속도에 영향을 주지 않습니다. 등록기관 최소값을 사용하세요.",
                "Gabia 사용 시: DNS 레코드 입력 후 반드시 '확인 후 저장' 버튼을 클릭해야 적용됩니다.",
            ]
            if not records:
                base_warnings.append("APEX_ADDRESS strategy is selected, but CUSTOM_DOMAIN_IP_TARGETS is not configured.")
            return records, base_warnings


def _checklist(
    state: DomainSetupState,
    *,
    certificate_ready: bool = False,
) -> list[DomainSetupChecklistItem]:
    purchase_done = state.management_mode == DomainManagementMode.HOSPITAL_MANAGED or bool(
        state.registrar
    )
    # DM-U5: 체크리스트는 각 단계의 실제 상태를 반영. site_live는 DNS 검증 완료 여부만 나타냄.
    dns_verified = bool(state.dns_verified_at)
    cert_done = state.cert_job_state == "DONE" if state.cert_job_state else False
    
    return [
        DomainSetupChecklistItem(
            key="domain_saved",
            label="① 도메인 저장",
            description="병원 계정에 연결할 도메인을 저장합니다.",
            status="DONE" if state.domain else "PENDING",
        ),
        DomainSetupChecklistItem(
            key="purchase",
            label="② 구매/소유권 확인",
            description="병원 또는 MotionLabs가 도메인 구매와 갱신 책임자를 확정합니다.",
            status="DONE" if purchase_done else "PENDING",
        ),
        DomainSetupChecklistItem(
            key="dns_record",
            label="DNS 레코드 등록 (운영자)",
            description="설정표의 DNS 레코드를 등록기관 또는 DNS 제공자에 추가합니다. Gabia 사용 시 확인 후 저장 필수.",
            status="WAITING",
        ),
        DomainSetupChecklistItem(
            key="dns_verified",
            label="③ DNS 검증 (운영자 작업 완료)",
            description="DNS 레코드 등록 후 연결 검증을 실행합니다. 검증 성공 시 온보딩 5단계 완료.",
            status="DONE" if dns_verified else "PENDING",
        ),
        DomainSetupChecklistItem(
            key="certificate_ready",
            label="④ HTTPS 인증서 (시스템 후속)",
            description="인증서는 백그라운드에서 자동 발급됩니다.",
            status="DONE" if cert_done else "WAITING" if dns_verified else "PENDING",
        ),
    ]
