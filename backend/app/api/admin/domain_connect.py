import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.domain import _normalize_dns_name
from app.core.config import settings
from app.core.database import get_db
from app.models.hospital import DomainDnsStrategy, DomainManagementMode, Hospital, HospitalStatus
from app.services.audit_log import default_actor, write_audit_log
from app.services.site_revalidate import (
    ensure_site_revalidate_configured,
    trigger_hospital_site_revalidate_safe,
)
from app.utils.domain import is_valid_hostname, normalize_domain
from app.workers.tasks import build_aeo_site

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Domain Connect"])


class DomainConnect(BaseModel):
    domain: str = Field(min_length=1, max_length=500)
    domain_management_mode: DomainManagementMode | None = Field(
        default=None,
        validation_alias=AliasChoices("domain_management_mode", "management_mode"),
    )
    domain_dns_strategy: DomainDnsStrategy | None = Field(
        default=None,
        validation_alias=AliasChoices("domain_dns_strategy", "dns_strategy"),
    )
    domain_registrar: str | None = Field(
        default=None,
        max_length=200,
        validation_alias=AliasChoices("domain_registrar", "registrar"),
    )
    domain_dns_provider: str | None = Field(
        default=None,
        max_length=200,
        validation_alias=AliasChoices("domain_dns_provider", "dns_provider"),
    )
    domain_purchase_note: str | None = Field(
        default=None,
        max_length=2000,
        validation_alias=AliasChoices("domain_purchase_note", "purchase_note"),
    )


@router.patch("/{hospital_id}/domain")
async def connect_domain(
    hospital_id: uuid.UUID,
    body: DomainConnect,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    h = await _get_or_404(db, hospital_id)
    domain = _normalize_and_validate_domain(body.domain)
    await _ensure_domain_not_taken(db, hospital_id, domain)
    previous_domain = h.aeo_domain
    previous_status = h.status.value if hasattr(h.status, "value") else str(h.status)
    previous_site_live = bool(h.site_live)
    previous_dns_strategy = getattr(h, "domain_dns_strategy", DomainDnsStrategy.CNAME)
    domain_changed = _normalize_dns_name(previous_domain) != _normalize_dns_name(domain)
    h.aeo_domain = domain
    changed_metadata = _apply_domain_metadata(h, body)
    strategy_changed = (
        "domain_dns_strategy" in changed_metadata and previous_dns_strategy != h.domain_dns_strategy
    )

    if domain_changed or strategy_changed:
        h.site_live = False
        if h.status == HospitalStatus.ACTIVE:
            h.status = HospitalStatus.PENDING_DOMAIN
    if previous_site_live:
        ensure_site_revalidate_configured()

    await write_audit_log(
        db,
        action="connect_domain",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="domain",
        target_id=domain,
        detail={
            "previous_domain": previous_domain,
            "new_domain": domain,
            "domain_changed": domain_changed,
            "previous_status": previous_status,
            "previous_site_live": previous_site_live,
            "previous_dns_strategy": _enum_value(previous_dns_strategy),
            "strategy_changed": strategy_changed,
            "new_status": h.status.value if hasattr(h.status, "value") else str(h.status),
            "changed_metadata": changed_metadata,
            "domain_management_mode": _enum_value(
                getattr(h, "domain_management_mode", DomainManagementMode.HOSPITAL_MANAGED)
            ),
            "domain_dns_strategy": _enum_value(
                getattr(h, "domain_dns_strategy", DomainDnsStrategy.CNAME)
            ),
            "domain_registrar": getattr(h, "domain_registrar", None),
            "domain_dns_provider": getattr(h, "domain_dns_provider", None),
            "domain_purchase_note": getattr(h, "domain_purchase_note", None),
        },
    )
    await db.commit()
    if previous_site_live:
        await trigger_hospital_site_revalidate_safe(h.slug, h.treatments, hospital_name=h.name)

    if (
        (domain_changed or not h.site_built)
        and bool(getattr(h, "profile_complete", False))
        and bool(getattr(h, "v0_report_done", False))
    ):
        background_tasks.add_task(
            build_aeo_site.apply_async,
            args=[str(hospital_id)],
            queue="default",
        )
        return {"detail": f"Domain {domain} set. Content hub exposure refresh triggered."}
    if domain_changed or not h.site_built:
        return {
            "detail": (
                f"Domain {domain} set. Content hub refresh will start after profile and V0 gates."
            )
        }
    return {"detail": f"Domain {domain} unchanged. No exposure refresh needed."}


def _apply_domain_metadata(h: Hospital, body: DomainConnect) -> list[str]:
    changed: list[str] = []
    for field in (
        "domain_management_mode",
        "domain_dns_strategy",
        "domain_registrar",
        "domain_dns_provider",
        "domain_purchase_note",
    ):
        if field not in body.model_fields_set:
            continue
        value = getattr(body, field)
        if value is None and field in {"domain_management_mode", "domain_dns_strategy"}:
            continue
        if getattr(h, field, None) != value:
            setattr(h, field, value)
            changed.append(field)
    return changed


def _normalize_and_validate_domain(raw: str) -> str:
    normalized = normalize_domain(raw)
    if not normalized or not is_valid_hostname(normalized):
        raise HTTPException(
            status_code=422,
            detail=(
                "유효한 도메인 형식이 아닙니다. "
                "스킴(https://)이나 경로 없이 호스트명만 입력해 주세요. 예: info.jangpyeon.com"
            ),
        )
    protected_domain = _matching_platform_domain(normalized)
    if protected_domain:
        raise HTTPException(
            status_code=422,
            detail=(
                f"플랫폼 기본 도메인({protected_domain})은 병원 도메인으로 사용할 수 없습니다. "
                "병원이 보유한 별도 도메인을 입력해 주세요."
            ),
        )
    return normalized


def _matching_platform_domain(normalized: str) -> str | None:
    protected_domains = {
        domain
        for domain in (
            normalize_domain(settings.CNAME_TARGET),
            normalize_domain(urlparse(settings.SITE_BASE_URL).hostname or ""),
            normalize_domain(urlparse(settings.ADMIN_BASE_URL).hostname or ""),
        )
        if domain
    }
    for protected in protected_domains:
        if normalized == protected or normalized.endswith(f".{protected}"):
            return protected
    return None


async def _ensure_domain_not_taken(
    db: AsyncSession, hospital_id: uuid.UUID, normalized_domain: str
) -> None:
    result = await db.execute(
        select(Hospital)
        .where(
            func.lower(Hospital.aeo_domain) == normalized_domain,
            Hospital.id != hospital_id,
        )
        .limit(1)
    )
    other = result.scalars().first()
    if other:
        raise HTTPException(
            status_code=409,
            detail=f"이미 다른 병원({other.name})에 연결된 도메인입니다. 도메인을 확인해 주세요.",
        )


async def _get_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


def _enum_value(value: str | DomainManagementMode | DomainDnsStrategy) -> str:
    return value.value if hasattr(value, "value") else str(value)
