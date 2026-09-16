"""Bounded public tenant checks; transient transport errors are not DNS diagnoses."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

logger = logging.getLogger(__name__)
_RETRYABLE = {
    "timeout",
    "tls_or_network_error",
    "http_408",
    "http_429",
    "http_500",
    "http_502",
    "http_503",
    "http_504",
}
_MAX_RECHECK_DELAY = 2.0


@dataclass(frozen=True, slots=True)
class _Probe:
    healthy: bool
    reason: str
    retry_after: str | None = None


def _check_once(client: httpx.Client, domain: str, hospital_id: uuid.UUID, slug: str) -> _Probe:
    try:
        response = client.get(f"https://{domain}/.well-known/reputation-health")
    except httpx.TimeoutException:
        return _Probe(False, "timeout")
    except httpx.HTTPError:
        return _Probe(False, "tls_or_network_error")
    if 300 <= response.status_code < 400:
        return _Probe(False, "redirect_not_allowed")
    if response.status_code != 200:
        return _Probe(False, f"http_{response.status_code}", response.headers.get("retry-after"))
    try:
        marker = response.json()
    except ValueError:
        return _Probe(False, "invalid_tenant_marker")
    if not isinstance(marker, dict):
        return _Probe(False, "invalid_tenant_marker")
    valid = (
        marker.get("hospital_id") == str(hospital_id)
        and marker.get("slug") == slug
        and marker.get("canonical_host") == domain
        and isinstance(marker.get("release"), str)
        and bool(marker["release"].strip())
    )
    return _Probe(valid, "tenant_marker_ok" if valid else "tenant_marker_mismatch")


def _recheck_delay(probe: _Probe) -> float | None:
    if probe.reason not in _RETRYABLE:
        return None
    if probe.retry_after is None:
        return 1.0
    try:
        raw = probe.retry_after.strip()
        delay = (
            float(int(raw))
            if raw.isdigit()
            else (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds()
        )
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0.0, delay) if delay <= _MAX_RECHECK_DELAY else None


def check_custom_domain_https(
    client: httpx.Client, domain: str, *, expected_hospital_id: uuid.UUID, expected_slug: str
) -> tuple[bool, str]:
    """One normal check plus at most one transient recheck on the same host.

    Rechecks never relax TLS, redirect or tenant identity checks. A long or invalid
    Retry-After defers to the next scheduled check rather than hammering the host.
    Durable failure/recovery recording remains owned by the existing worker.
    """
    first = _check_once(client, domain, expected_hospital_id, expected_slug)
    delay = _recheck_delay(first)
    if first.healthy or delay is None:
        return first.healthy, first.reason
    time.sleep(delay)
    final = _check_once(client, domain, expected_hospital_id, expected_slug)
    logger.info(
        "domain health bounded recheck: hospital_id=%s host=%s initial=%s final=%s",
        expected_hospital_id,
        domain,
        first.reason,
        final.reason,
    )
    return final.healthy, final.reason
