import asyncio
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from limits import parse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.models.admin_user import AdminUser
from app.services.admin_passwords import hash_admin_password, verify_admin_password
from app.services.admin_session_revocation import (
    AdminSessionRevocationUnavailable,
    is_admin_session_hash_revoked,
    revoke_admin_session_hash,
)

router = APIRouter(prefix="/admin/auth", tags=["Admin — Auth"])

# CDX-M3: 로그인 brute-force 스로틀의 단일 진실 — Redis 공유 저장소(slowapi limiter)에
# 실패 횟수를 기록하므로 admin BFF의 프로세스-로컬 Map(서버리스 인스턴스별)과 달리
# 모든 인스턴스에 걸쳐 전역으로 적용된다. 실패만 카운트, 성공 시 해제.
_LOGIN_RATE_LIMIT = parse("5/15minute")


class AdminLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=500)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned:
            raise ValueError("valid email is required")
        return cleaned


class AdminAccountResponse(BaseModel):
    id: UUID
    email: str
    name: str
    role: str


class AdminSessionRevocationRequest(BaseModel):
    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime


class AdminSessionRevocationResponse(BaseModel):
    revoked: bool


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    """존재하지 않는 계정에도 동일한 해시 비용을 지불하기 위한 고정 더미 해시."""
    return hash_admin_password("timing-equalizer-dummy-password")


def _login_rate_limit_strategy(request: Request | None):
    if request is None:
        return None
    limiter = getattr(getattr(request.app, "state", None), "limiter", None)
    if limiter is None or not getattr(limiter, "enabled", True):
        return None
    return limiter.limiter


def _login_email_throttle_key(email: str) -> str:
    return f"admin-login:email:{email}"


def _login_throttle_keys(request: Request, email: str) -> list[str]:
    # 이메일 키는 IP 로테이션으로 우회할 수 없고, IP 키는 다수 계정 스프레이를 막는다.
    # IP는 get_request_ip 기준 — admin BFF가 SITE_BFF_SECRET으로 인증한 X-Visitor-IP를
    # 보내면 실제 AE 방문자 IP가, 아니면 신뢰 프록시 체인 기준 IP가 잡힌다.
    keys = [_login_email_throttle_key(email)]
    ip = get_request_ip(request)
    if ip:
        keys.append(f"admin-login:ip:{ip}")
    return keys


@router.post("/login", response_model=AdminAccountResponse)
async def login_admin(
    body: AdminLoginRequest,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    strategy = _login_rate_limit_strategy(request)
    throttle_keys = _login_throttle_keys(request, body.email) if strategy else []
    if strategy and not all(strategy.test(_LOGIN_RATE_LIMIT, key) for key in throttle_keys):
        raise HTTPException(status_code=429, detail="Too many login attempts")

    result = await db.execute(select(AdminUser).where(AdminUser.email == body.email))
    user = result.scalar_one_or_none()
    # 항상 PBKDF2를 1회 수행 — 미존재 계정에서 해시를 건너뛰면 ~수백 ms 타이밍 오라클로
    # 관리자 계정 존재 여부가 노출된다. 600k iteration CPU 작업은 이벤트 루프를 멈추지
    # 않도록 워커 스레드에서 실행.
    password_hash = user.password_hash if user else _dummy_password_hash()
    valid = await asyncio.to_thread(verify_admin_password, body.password, password_hash)
    if not user or not user.is_active or not valid:
        if strategy:
            for key in throttle_keys:
                strategy.hit(_LOGIN_RATE_LIMIT, key)
        raise HTTPException(status_code=401, detail="Invalid admin credentials")

    if strategy:
        # 성공 시 이메일 키만 해제한다 (R7). IP 키까지 지우면 공격자가 자기 계정으로
        # 한 번 로그인할 때마다 IP 카운터가 리셋돼 단일 IP에서 다계정 password spraying이
        # 가능해진다. IP 키는 윈도우(15분) 만료로 자연 소멸한다.
        strategy.clear(_LOGIN_RATE_LIMIT, _login_email_throttle_key(body.email))

    user.last_login_at = datetime.now(UTC)
    await db.commit()
    return AdminAccountResponse(id=user.id, email=user.email, name=user.name, role=user.role)


@router.post("/sessions/revoke", response_model=AdminSessionRevocationResponse)
async def revoke_admin_session(body: AdminSessionRevocationRequest):
    try:
        await revoke_admin_session_hash(body.token_hash, expires_at=body.expires_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid admin session token hash") from exc
    except AdminSessionRevocationUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Admin session revocation state unavailable",
        ) from exc
    return AdminSessionRevocationResponse(revoked=True)


@router.get("/sessions/{token_hash}/revocation", response_model=AdminSessionRevocationResponse)
async def get_admin_session_revocation(token_hash: str):
    try:
        revoked = await is_admin_session_hash_revoked(token_hash)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid admin session token hash") from exc
    except AdminSessionRevocationUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Admin session revocation state unavailable",
        ) from exc
    return AdminSessionRevocationResponse(revoked=revoked)
