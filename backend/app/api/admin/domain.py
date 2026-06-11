"""
Admin API — 공개 도메인 상태 검증
POST /admin/hospitals/{id}/domain/verify — CNAME 확인 + ACTIVE 전환
"""
import asyncio
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


def missing_live_prerequisites(hospital: Hospital) -> list[str]:
    """LIVE(site_live/ACTIVE) 전환 전 충족돼야 하는 단계 — operations verify-domain과 공유.

    DNS만 맞으면 곧바로 라이브가 되어 V0/허브 빌드/스케줄을 건너뛰는 우회 경로를 막는다 (P1-6).
    """
    return [
        label
        for label, ready in (
            ("V0 리포트", hospital.v0_report_done),
            ("병원 정보 허브 빌드", hospital.site_built),
            ("콘텐츠 스케줄", hospital.schedule_set),
        )
        if not ready
    ]


@router.post("/{hospital_id}/domain/verify", response_model=DomainVerifyResponse)
async def verify_domain(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    병원 공개 도메인의 CNAME을 DNS 조회로 검증한다.
    검증 성공 + 사전 단계(V0/허브 빌드/스케줄) 충족 시 site_live = True, ACTIVE 전환.
    """
    hospital = await _get_hospital_or_404(db, hospital_id)

    if not hospital.aeo_domain:
        raise HTTPException(status_code=400, detail="도메인이 설정되지 않았습니다. 먼저 도메인을 입력해 주세요.")

    domain = hospital.aeo_domain
    cname_value = await _resolve_cname(domain)
    verified = _normalize_dns_name(cname_value) == _normalize_dns_name(settings.CNAME_TARGET)

    if verified:
        # operations.py verify-domain / hospitals.py activate와 동일한 게이트 (P1-6).
        missing = missing_live_prerequisites(hospital)
        if missing:
            raise HTTPException(
                status_code=409,
                detail=f"도메인 DNS는 확인됐지만 LIVE 전환 전 단계가 남아 있습니다: {', '.join(missing)}",
            )
        hospital.site_live = True
        hospital.status = HospitalStatus.ACTIVE
        await db.commit()
        message = f"공개 도메인 상태가 확인되었습니다. ({domain} → {cname_value})"
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
async def _resolve_cname(domain: str) -> str | None:
    """DNS CNAME 조회. 실패 시 None 반환.

    동기 DNS 해석은 응답 없는 네임서버에서 수 초를 블로킹한다 — 단일 uvicorn 프로세스의
    이벤트 루프가 멈추면 공개 표면 요청까지 함께 지연되므로 워커 스레드에서 실행한다.
    """
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


def _normalize_dns_name(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().rstrip(".").lower()


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h
