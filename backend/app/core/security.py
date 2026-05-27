"""Admin API 인증 — X-Admin-Key 헤더 검증 + rate limiting"""
import secrets

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings

api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=True)


async def verify_admin_key(key: str = Security(api_key_header)) -> str:
    if not secrets.compare_digest(key.encode("utf-8"), settings.ADMIN_SECRET_KEY.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return key


async def verify_admin_rate_limit(request: Request) -> None:
    """Rate limit admin API calls. Relies on slowapi limiter being mounted at app.state."""
    limiter = getattr(request.app.state, "limiter", None)
    if limiter is None:
        return
    ip = request.client.host if request.client else "unknown"
    limit_key = f"admin:{ip}"
    if not await limiter.check_request(request, limit_key, ["100/minute"]):
        raise HTTPException(status_code=429, detail="Too many requests")
