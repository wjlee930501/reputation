"""Admin API 인증 — X-Admin-Key 헤더 검증"""
import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings

api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=True)


async def verify_admin_key(key: str = Security(api_key_header)) -> str:
    expected = settings.ADMIN_SECRET_KEY
    if expected == "change-me":
        raise HTTPException(status_code=500, detail="ADMIN_SECRET_KEY not configured")
    if not secrets.compare_digest(key.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return key
