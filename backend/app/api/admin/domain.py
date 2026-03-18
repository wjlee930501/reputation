"""
Admin API — 도메인 검증
POST /admin/hospitals/{id}/domain/verify — CNAME 검증 + ACTIVE 전환
"""
import socket
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.hospital import Hospital, HospitalStatus

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Domain"])


class DomainVerifyResponse(BaseModel):
    domain: str
    verified: bool
    cname_value: str | None
    expected_cname: str
    message: str


@router.post("/{hospital_id}/domain/verify", response_model=DomainVerifyResponse)
async def verify_domain(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    병원 aeo_domain의 CNAME을 DNS 조회로 검증한다.
    검증 성공 시 hospital.site_live = True, status = ACTIVE 전환.
    """
    hospital = await _get_hospital_or_404(db, hospital_id)

    if not hospital.aeo_domain:
        raise HTTPException(status_code=400, detail="도메인이 설정되지 않았습니다. 먼저 도메인을 입력해 주세요.")

    domain = hospital.aeo_domain
    cname_value = _resolve_cname(domain)
    verified = cname_value is not None and settings.CNAME_TARGET in cname_value

    if verified:
        hospital.site_live = True
        if hospital.schedule_set:
            hospital.status = HospitalStatus.ACTIVE
        # else: leave status as-is, don't regress to PENDING_DOMAIN
        await db.commit()
        message = f"도메인 연결이 확인되었습니다. ({domain} → {cname_value})"
    else:
        message = (
            f"CNAME 검증 실패. DNS에 CNAME 레코드를 추가해 주세요: "
            f"{domain} → {settings.CNAME_TARGET}"
        )

    return DomainVerifyResponse(
        domain=domain,
        verified=verified,
        cname_value=cname_value,
        expected_cname=settings.CNAME_TARGET,
        message=message,
    )


# ── 헬퍼 ─────────────────────────────────────────────────────────
def _resolve_cname(domain: str) -> str | None:
    """DNS CNAME 조회. 실패 시 None 반환."""
    try:
        # socket.getaddrinfo로 실제 해석된 호스트명 확인
        # 더 정확한 CNAME 조회를 위해 dnspython이 있으면 사용, 없으면 socket fallback
        try:
            import dns.resolver
            answers = dns.resolver.resolve(domain, "CNAME")
            return str(answers[0].target).rstrip(".")
        except ImportError:
            pass

        # fallback: getfqdn으로 최종 호스트 확인
        resolved = socket.getfqdn(domain)
        return resolved if resolved != domain else None
    except Exception:
        return None


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h
