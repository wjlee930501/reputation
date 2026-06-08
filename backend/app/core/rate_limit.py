"""Shared slowapi limiter — imported by `main.py` and any router that needs it.

Defining the limiter in its own module avoids the circular import that would
otherwise arise from `main.py` importing routers that import the limiter.
"""
from ipaddress import ip_address, ip_network

from slowapi import Limiter
from starlette.requests import Request

from app.core.config import settings


def get_request_ip(request: Request) -> str | None:
    """Return the real client IP, honoring proxy headers only from trusted hops.

    Walks X-Forwarded-For RIGHT-TO-LEFT and returns the first entry that is NOT a
    trusted proxy. The LEFTMOST entry is client-supplied and therefore spoofable —
    using it (as before) lets a caller forge their IP to evade rate limits or write
    an arbitrary consent_ip. The rightmost-untrusted entry is the one the nearest
    trusted proxy (the LB) actually observed, which a client cannot control.

    Requires TRUSTED_PROXY_IPS to list the real proxy/LB ranges. A 0.0.0.0/0 value
    would mark every hop trusted and skip the whole chain (rejected in prod config).
    """
    remote = request.client.host if request.client else None
    if not _is_trusted_proxy(remote):
        return remote

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        for candidate in reversed([part.strip() for part in forwarded.split(",")]):
            if _is_valid_ip(candidate) and not _is_trusted_proxy(candidate):
                return candidate
    return remote


def _is_trusted_proxy(value: str | None) -> bool:
    if not value:
        return False
    trusted = {item.strip() for item in settings.TRUSTED_PROXY_IPS if item.strip()}
    try:
        remote = ip_address(value)
    except ValueError:
        return False
    if "*" in trusted and settings.APP_ENV.lower() != "production":
        return True
    for item in trusted:
        try:
            if remote in ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def _is_valid_ip(value: str | None) -> bool:
    if not value:
        return False
    try:
        ip_address(value)
        return True
    except ValueError:
        return False


def _rate_limit_key(request: Request) -> str:
    return get_request_ip(request) or "unknown"


limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=settings.REDIS_URL,
    default_limits=["60/minute"],
)
