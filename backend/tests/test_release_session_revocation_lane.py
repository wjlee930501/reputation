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


async def test_session_check_uses_its_own_bucket_not_the_shared_admin_budget():
    """9/20: BFF IP 하나가 요청과 폐기 확인을 한 버킷에서 소진해 확인이 429 → BFF 503이 됐다."""
    limiter = SimpleNamespace(enabled=True, limiter=FixedWindowRateLimiter(MemoryStorage()))
    app = SimpleNamespace(state=SimpleNamespace(limiter=limiter))

    def request(method, path):
        return Request({"type": "http", "method": method, "path": path,
                        "client": ("127.0.0.1", 12345), "headers": [], "app": app})

    reads = request("GET", "/api/v1/admin/operations/overview")
    check = request("GET", f"/api/v1/admin/auth/sessions/{'a' * 64}/revocation")
    for _ in range(100):
        await verify_admin_rate_limit(reads)
    # 요청 버킷이 찼어도 폐기 확인은 자기 버킷으로 통과한다.
    for _ in range(100):
        await verify_admin_rate_limit(check)
    with pytest.raises(HTTPException) as bounded:
        await verify_admin_rate_limit(check)
    assert bounded.value.status_code == 429, "확인 버킷도 무제한이 아니다"
    with pytest.raises(HTTPException) as reads_blocked:
        await verify_admin_rate_limit(reads)
    assert reads_blocked.value.status_code == 429

    # 같은 경로라도 형식이 다른 해시나 다른 메서드는 공유 버킷에 남는다.
    fresh = SimpleNamespace(enabled=True, limiter=FixedWindowRateLimiter(MemoryStorage()))
    app.state.limiter = fresh
    odd = request("GET", "/api/v1/admin/auth/sessions/not-a-hash/revocation")
    for _ in range(100):
        await verify_admin_rate_limit(odd)
    with pytest.raises(HTTPException):
        await verify_admin_rate_limit(request("GET", "/api/v1/admin/hospitals"))
