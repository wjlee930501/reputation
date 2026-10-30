"""Fleet-wide circuit for OpenRouter API-key monthly spending limits.

The provider returns this account condition as HTTP 403 with the stable message
``Key limit exceeded``.  It is not a transient provider outage: retrying every
content slot only buys identical failures.  The circuit is shared through Redis,
scoped to the configured key fingerprint and UTC calendar month, and therefore
does not carry over after a key rotation or the next monthly period.

Redis is an optimisation, not the durable source of an individual slot's state.
Callers must still persist ``OPENROUTER_KEY_LIMIT_EXCEEDED`` on each attempted
slot.  Redis failure is fail-open so a control-plane outage cannot permanently
disable generation; the original provider failure still reaches Operations.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import openai
import redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.services import openrouter

logger = logging.getLogger(__name__)

OPENROUTER_KEY_LIMIT_EXCEEDED_CODE = "OPENROUTER_KEY_LIMIT_EXCEEDED"
OPENROUTER_KEY_LIMIT_EXCEEDED_MESSAGE = (
    "OpenRouter API 키의 월 사용 한도가 소진되어 자동 콘텐츠 생성을 중단했습니다."
)
_ERROR_MARKER = "key limit exceeded"
_CIRCUIT_PREFIX = "provider-circuit:openrouter:key-limit"
# The period is encoded in the key, but expiry also prevents stale-key buildup.
_CIRCUIT_TTL_SECONDS = 40 * 24 * 60 * 60


class OpenRouterKeyLimitCircuitOpen(RuntimeError):
    """Raised before a provider call when another worker observed the monthly limit."""


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return tuple(chain)


def is_openrouter_key_limit_error(exc: BaseException) -> bool:
    """Return true only for OpenRouter's specific monthly key-limit 403."""

    for candidate in _exception_chain(exc):
        if not isinstance(candidate, (openai.PermissionDeniedError, httpx.HTTPStatusError)):
            continue
        response = getattr(candidate, "response", None)
        status_code = getattr(candidate, "status_code", None) or getattr(
            response, "status_code", None
        )
        if status_code != 403:
            continue
        if _ERROR_MARKER in openrouter._normalized_error_text(candidate):
            return True
    return False


def billing_period(now: datetime | None = None) -> str:
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return observed.astimezone(UTC).strftime("%Y-%m")


def attempt_period_is_current(value: object, now: datetime | None = None) -> bool:
    return isinstance(value, str) and value == billing_period(now)


def key_fingerprint() -> str:
    value = settings.OPENROUTER_API_KEY.strip().encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:16]


def circuit_key(
    now: datetime | None = None,
    *,
    fingerprint: str | None = None,
    period: str | None = None,
) -> str:
    return f"{_CIRCUIT_PREFIX}:{fingerprint or key_fingerprint()}:{period or billing_period(now)}"


def _connect() -> redis.Redis:
    return redis.Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )


def trip_circuit(
    *, now: datetime | None = None, redis_client: Any | None = None
) -> bool:
    """Open the shared circuit and return whether this caller was the first detector.

    On Redis failure, return true so the original provider failure still owns an
    operator notification.  Other workers may also notify during that control-plane
    failure, which is preferable to suppressing the only actionable signal.
    """

    if redis_client is None and not os.environ.get("REDIS_URL"):
        logger.warning("OpenRouter key-limit circuit is fail-open without REDIS_URL")
        return True
    client = redis_client or _connect()
    owned = redis_client is None
    try:
        return bool(
            client.set(
                circuit_key(now),
                (now or datetime.now(UTC)).isoformat(),
                nx=True,
                ex=_CIRCUIT_TTL_SECONDS,
            )
        )
    except (OSError, RedisError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        logger.warning(
            "OpenRouter key-limit circuit could not be opened: %s", type(exc).__name__
        )
        return True
    finally:
        if owned:
            try:
                client.close()
            except (OSError, RedisError):
                pass


def circuit_is_open(
    *,
    now: datetime | None = None,
    redis_client: Any | None = None,
    fingerprint: str | None = None,
    period: str | None = None,
) -> bool:
    """Read the shared circuit. Redis failure deliberately fails open."""

    if redis_client is None and not os.environ.get("REDIS_URL"):
        return False
    client = redis_client or _connect()
    owned = redis_client is None
    try:
        return bool(client.exists(circuit_key(now, fingerprint=fingerprint, period=period)))
    except (OSError, RedisError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        logger.warning(
            "OpenRouter key-limit circuit could not be read: %s", type(exc).__name__
        )
        return False
    finally:
        if owned:
            try:
                client.close()
            except (OSError, RedisError):
                pass


def close_circuit(
    *,
    now: datetime | None = None,
    redis_client: Any | None = None,
    fingerprint: str | None = None,
    period: str | None = None,
) -> bool:
    """Close one key/period circuit after a successful bounded provider probe."""

    if redis_client is None and not os.environ.get("REDIS_URL"):
        return False
    client = redis_client or _connect()
    owned = redis_client is None
    try:
        return bool(client.delete(circuit_key(now, fingerprint=fingerprint, period=period)))
    except (OSError, RedisError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        logger.warning(
            "OpenRouter key-limit circuit could not be closed: %s", type(exc).__name__
        )
        return False
    finally:
        if owned:
            try:
                client.close()
            except (OSError, RedisError):
                pass


def raise_if_circuit_open() -> None:
    if circuit_is_open():
        raise OpenRouterKeyLimitCircuitOpen(OPENROUTER_KEY_LIMIT_EXCEEDED_MESSAGE)


__all__ = [
    "OPENROUTER_KEY_LIMIT_EXCEEDED_CODE",
    "OPENROUTER_KEY_LIMIT_EXCEEDED_MESSAGE",
    "OpenRouterKeyLimitCircuitOpen",
    "attempt_period_is_current",
    "billing_period",
    "close_circuit",
    "circuit_is_open",
    "circuit_key",
    "is_openrouter_key_limit_error",
    "key_fingerprint",
    "raise_if_circuit_open",
    "trip_circuit",
]
