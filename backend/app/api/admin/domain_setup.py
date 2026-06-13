import ipaddress
import uuid
from dataclasses import dataclass
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.hospital import DomainDnsStrategy, DomainManagementMode, Hospital

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Domain Setup"])
DomainEnumT = TypeVar("DomainEnumT", DomainManagementMode, DomainDnsStrategy)


class DomainSetupRecord(BaseModel):
    type: str
    name: str
    host: str
    value: str
    ttl: str = "300"
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


@router.get("/{hospital_id}/domain/setup", response_model=DomainSetupResponse)
async def get_domain_setup(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")

    state = _domain_setup_state(hospital)
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
        records=records,
        checklist=_checklist(state),
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
    return [str(value) for value in sorted(set(values), key=lambda value: (value.version, str(value)))]


def _domain_records(
    domain: str | None,
    strategy: DomainDnsStrategy,
    addresses: list[str],
) -> tuple[list[DomainSetupRecord], list[str]]:
    if not domain:
        return [], []
    match strategy:
        case DomainDnsStrategy.CNAME:
            return [
                DomainSetupRecord(
                    type="CNAME",
                    name=domain,
                    host=domain,
                    value=settings.CNAME_TARGET,
                    purpose="병원 정보 허브 트래픽을 Reputation 플랫폼으로 연결",
                )
            ], []
        case DomainDnsStrategy.APEX_ADDRESS:
            records = [
                DomainSetupRecord(
                    type="AAAA" if ":" in address else "A",
                    name=domain,
                    host=domain,
                    value=address,
                    purpose="루트 도메인을 Reputation 글로벌 로드밸런서로 연결",
                )
                for address in addresses
            ]
            warnings = (
                []
                if records
                else ["APEX_ADDRESS strategy is selected, but CUSTOM_DOMAIN_IP_TARGETS is not configured."]
            )
            return records, warnings


def _checklist(state: DomainSetupState) -> list[DomainSetupChecklistItem]:
    purchase_done = state.management_mode == DomainManagementMode.HOSPITAL_MANAGED or bool(state.registrar)
    return [
        DomainSetupChecklistItem(
            key="domain_saved",
            label="도메인 저장",
            description="병원 계정에 연결할 도메인을 저장합니다.",
            status="DONE" if state.domain else "PENDING",
        ),
        DomainSetupChecklistItem(
            key="purchase",
            label="구매/소유권 확인",
            description="병원 또는 MotionLabs가 도메인 구매와 갱신 책임자를 확정합니다.",
            status="DONE" if purchase_done else "PENDING",
        ),
        DomainSetupChecklistItem(
            key="dns_record",
            label="DNS 레코드 등록",
            description="설정표의 DNS 레코드를 등록기관 또는 DNS 제공자에 추가합니다.",
            status="PENDING",
        ),
        DomainSetupChecklistItem(
            key="dns_verified",
            label="DNS 검증",
            description="DNS 전파 후 연결 검증을 실행합니다.",
            status="PENDING",
        ),
        DomainSetupChecklistItem(
            key="certificate_ready",
            label="HTTPS 인증서",
            description="인증서가 준비되면 병원 도메인으로 허브를 제공합니다.",
            status="PENDING",
        ),
    ]
