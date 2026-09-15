"""Read traffic must not starve authenticated session revocation."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from starlette.requests import Request

from app.core.security import verify_admin_rate_limit


async def test_logout_has_a_separate_bounded_rate_lane():
    limiter = SimpleNamespace(enabled=True, limiter=FixedWindowRateLimiter(MemoryStorage()))
    app = SimpleNamespace(state=SimpleNamespace(limiter=limiter))
    def request(method, path):
        return Request({"type": "http", "method": method, "path": path,
                        "client": ("127.0.0.1", 12345), "headers": [], "app": app})
    reads = request("GET", "/api/v1/admin/hospitals")
    for _ in range(100):
        await verify_admin_rate_limit(reads)
    with pytest.raises(HTTPException) as blocked:
        await verify_admin_rate_limit(reads)
    assert blocked.value.status_code == 429
    revoke = request("POST", "/api/v1/admin/auth/sessions/revoke")
    for _ in range(30):
        await verify_admin_rate_limit(revoke)
    with pytest.raises(HTTPException) as bounded:
        await verify_admin_rate_limit(revoke)
    assert bounded.value.status_code == 429
