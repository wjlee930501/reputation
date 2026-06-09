"""Shared slowapi limiter — imported by `main.py` and any router that needs it.

Defining the limiter in its own module avoids the circular import that would
otherwise arise from `main.py` importing routers that import the limiter.
"""
import hmac
from ipaddress import ip_address, ip_network

from slowapi import Limiter
from starlette.requests import Request

from app.core.config import settings


def get_request_ip(request: Request) -> str | None:
    """Return the real client IP, honoring proxy headers only from trusted hops.

    Order of precedence:
    1. X-Visitor-IP authenticated by the site BFF shared secret (CDX-M1) — the
       browser→Vercel BFF→LB chain hides the visitor behind the Vercel egress IP,
       so the BFF forwards the visitor IP explicitly and proves itself with
       SITE_BFF_SECRET. Without the secret the header is ignored.
    2. X-Forwarded-For walked RIGHT-TO-LEFT: the first entry that is NOT a trusted
       proxy. The LEFTMOST entry is client-supplied and therefore spoofable — using
       it lets a caller forge their IP to evade rate limits or write an arbitrary
       consent_ip. The rightmost-untrusted entry is the one the nearest trusted
       proxy (the LB) actually observed, which a client cannot control.

    Requires TRUSTED_PROXY_IPS to list the real proxy/LB ranges. A 0.0.0.0/0 value
    would mark every hop trusted and skip the whole chain (rejected in prod config).
    """
    bff_visitor_ip = _bff_authenticated_visitor_ip(request)
    if bff_visitor_ip:
        return bff_visitor_ip

    remote = request.client.host if request.client else None
    if not _is_trusted_proxy(remote):
        return remote

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        for candidate in reversed([part.strip() for part in forwarded.split(",")]):
            if _is_valid_ip(candidate) and not _is_trusted_proxy(candidate):
                return candidate
    return remote


def _bff_authenticated_visitor_ip(request: Request) -> str | None:
    """Visitor IP forwarded by the site BFF, adopted only with a valid shared secret.

    Secret comparison is constant-time; an invalid or missing secret silently falls
    back to the XFF walk so a forged header can never influence the result.
    """
    secret = settings.SITE_BFF_SECRET.strip()
    if not secret:
        return None
    provided = request.headers.get("x-bff-auth") or ""
    if not hmac.compare_digest(provided.encode("utf-8"), secret.encode("utf-8")):
        return None
    candidate = (request.headers.get("x-visitor-ip") or "").strip()
    if _is_valid_ip(candidate):
        return candidate
    return None


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
