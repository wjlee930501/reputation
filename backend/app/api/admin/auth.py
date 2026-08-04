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
#
# 계층형 스로틀 (#4): IP 키는 촘촘하게(5/15분) 유지해 단일 출처의 무차별 대입을 막고,
# email 키는 임계값을 크게(20/시간) 둔다. email 키가 IP 무관 전역이라 임계값이 낮으면
# 이메일만 아는 공격자가 소수의 실패로 정상 사용자를 락아웃시키는 원격 DoS가 가능하기
# 때문이다. 로그인 성공 시 해당 email 카운터는 즉시 해제한다.
_LOGIN_IP_RATE_LIMIT = parse("5/15minute")
_LOGIN_EMAIL_RATE_LIMIT = parse("20/hour")


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


def _login_throttle_limits(request: Request, email: str) -> list[tuple[object, str]]:
    # 이메일 키는 IP 로테이션으로 우회할 수 없고, IP 키는 다수 계정 스프레이를 막는다.
    # IP는 get_request_ip 기준 — admin BFF가 SITE_BFF_SECRET으로 인증한 X-Visitor-IP를
    # 보내면 실제 AE 방문자 IP가, 아니면 신뢰 프록시 체인 기준 IP가 잡힌다.
    # (limit, key) 쌍으로 반환 — email과 IP에 서로 다른 임계값을 적용한다 (#4).
    limits: list[tuple[object, str]] = [
        (_LOGIN_EMAIL_RATE_LIMIT, _login_email_throttle_key(email)),
    ]
    ip = get_request_ip(request)
    if ip:
        limits.append((_LOGIN_IP_RATE_LIMIT, f"admin-login:ip:{ip}"))
    return limits


@router.post("/login", response_model=AdminAccountResponse)
async def login_admin(
    body: AdminLoginRequest,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    strategy = _login_rate_limit_strategy(request)
    throttle_limits = _login_throttle_limits(request, body.email) if strategy else []
    if strategy and not all(strategy.test(limit, key) for limit, key in throttle_limits):
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
            for limit, key in throttle_limits:
                strategy.hit(limit, key)
        raise HTTPException(status_code=401, detail="Invalid admin credentials")

    if strategy:
        # 성공 시 이메일 키만 해제한다 (R7). IP 키까지 지우면 공격자가 자기 계정으로
        # 한 번 로그인할 때마다 IP 카운터가 리셋돼 단일 IP에서 다계정 password spraying이
        # 가능해진다. IP 키는 윈도우(15분) 만료로 자연 소멸한다.
        strategy.clear(_LOGIN_EMAIL_RATE_LIMIT, _login_email_throttle_key(body.email))

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
async def get_admin_session_revocation(
    token_hash: str,
    account_id: UUID | None = None,
    issued_at: datetime | None = None,
    db: AsyncSession = Depends(get_db),
):
    """세션 폐기 여부. `account_id`가 오면 계정 상태까지 함께 본다.

    비활성화된 계정의 세션 쿠키는 그 자체로는 만료(최대 7일)까지 유효하고, 발급된
    토큰을 서버가 열거할 수 없어 개별 폐기도 불가능하다. Admin BFF가 이미 요청마다
    호출하는 이 경로에서 계정 상태를 같이 판정하면, 왕복을 늘리지 않고 비활성화가
    즉시(쓰기) / 폐기 캐시 TTL 안에(읽기) 반영된다.

    `account_id`가 없으면 종전과 동일하게 해시 폐기 여부만 본다.
    """
    try:
        revoked = await is_admin_session_hash_revoked(token_hash)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid admin session token hash") from exc
    except AdminSessionRevocationUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Admin session revocation state unavailable",
        ) from exc

    if not revoked and account_id is not None:
        result = await db.execute(
            select(AdminUser.is_active, AdminUser.sessions_invalid_before).where(
                AdminUser.id == account_id
            )
        )
        row = result.one_or_none()
        # 계정이 사라졌거나 비활성이면 세션도 더 이상 유효하지 않다.
        if row is None or not row.is_active:
            revoked = True
        elif row.sessions_invalid_before is not None:
            # 비밀번호 재설정·재활성화로 기준선이 올라가면 그 이전 발급분은 전부 무효.
            # 발급 시각을 모르는(구버전) 토큰은 기준선보다 앞선다고 볼 수밖에 없다 —
            # 세션 무효화가 "일부만 적용"되는 것보다 다시 로그인하게 하는 쪽이 안전하다.
            if issued_at is None or issued_at <= row.sessions_invalid_before:
                revoked = True

    return AdminSessionRevocationResponse(revoked=revoked)
