"""Admin API 인증 — X-Admin-Key 헤더 검증 + rate limiting"""
from collections.abc import AsyncGenerator
import secrets

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from limits import parse

from app.core.config import settings
from app.core.rate_limit import get_request_ip
from app.services.audit_log import reset_request_actor, set_request_actor

api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)
_ADMIN_RATE_LIMIT = parse("100/minute")


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


async def capture_admin_actor(request: Request) -> AsyncGenerator[None, None]:
    token = set_request_actor(request.headers.get("X-Admin-Actor"))
    try:
        yield
    finally:
        reset_request_actor(token)
