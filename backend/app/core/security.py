"""Admin API 인증 — X-Admin-Key 헤더 검증 + rate limiting"""
from collections.abc import AsyncGenerator
import re
import secrets

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from limits import parse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.models.admin_user import AdminUser
from app.services.audit_log import reset_request_actor, set_request_actor

api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)
_ADMIN_RATE_LIMIT = parse("100/minute")

# X-Admin-Actor는 Admin BFF가 세션 인증 후 전달하는 운영자 이메일이다. 헤더 자체는
# X-Admin-Key만 알면 위조할 수 있으므로, 값이 실제 활성 AdminUser.email과 매칭될 때만
# 채택하고, 형식이 다르거나 매칭되지 않으면 'unverified:{value}'로 표시해 감사 로그에서
# 위조 가능성을 드러낸다 (#5).
_ADMIN_ACTOR_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def verify_admin_key(key: str | None = Security(api_key_header)) -> str:
    admin_secret = settings.ADMIN_SECRET_KEY.strip()
    if not key or not admin_secret:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    if not secrets.compare_digest(key.encode("utf-8"), admin_secret.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return key


async def verify_admin_rate_limit(request: Request) -> None:
    """Rate limit admin API calls. Relies on slowapi limiter being mounted at app.state."""
    limiter = getattr(request.app.state, "limiter", None)
    if limiter is None or not getattr(limiter, "enabled", True):
        return
    strategy = limiter.limiter
    limit_key = f"admin:{get_request_ip(request) or 'unknown'}"
    if not strategy.hit(_ADMIN_RATE_LIMIT, limit_key):
        raise HTTPException(status_code=429, detail="Too many requests")


async def _resolve_admin_actor(db: AsyncSession, raw: str | None) -> str | None:
    """X-Admin-Actor 헤더를 검증해 감사 로그 actor로 채택할 값을 결정한다.

    - 헤더 없음 → None (audit_log가 ADMIN_ACTOR_NAME으로 폴백).
    - 이메일 형식이 아니거나 활성 AdminUser와 매칭 안 됨 → 'unverified:{value}'.
    - 활성 AdminUser.email과 매칭 → 정규 email 채택.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    if not _ADMIN_ACTOR_EMAIL_RE.match(cleaned):
        return f"unverified:{cleaned[:90]}"
    try:
        result = await db.execute(
            select(AdminUser.email).where(
                func.lower(AdminUser.email) == cleaned.lower(),
                AdminUser.is_active.is_(True),
            )
        )
        matched = result.scalar_one_or_none()
    except Exception:
        # DB 조회 실패 시 공유 세션이 실패 상태로 남으면 이후 엔드포인트 쿼리가
        # PendingRollbackError로 500이 된다 — 먼저 롤백해 세션을 회복시킨 뒤,
        # 헤더는 그대로 신뢰하지 않고 위조 가능성으로 표시한다.
        try:
            await db.rollback()
        except Exception:
            # 롤백 자체 실패(연결 끊김 등)도 actor 판정을 막지 않는다 — best-effort.
            pass
        return f"unverified:{cleaned[:90]}"
    if matched:
        return matched
    return f"unverified:{cleaned[:90]}"


async def capture_admin_actor(
    request: Request, db: AsyncSession = Depends(get_db)
) -> AsyncGenerator[None, None]:
    actor = await _resolve_admin_actor(db, request.headers.get("X-Admin-Actor"))
    token = set_request_actor(actor)
    try:
        yield
    finally:
        reset_request_actor(token)
