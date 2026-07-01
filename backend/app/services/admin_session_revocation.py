from datetime import UTC, datetime
import math
import re
from typing import Protocol

import redis.asyncio as redis_async
from redis.exceptions import RedisError

from app.core.config import settings


TOKEN_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
KEY_PREFIX = "admin-session-revoked:"


class AdminSessionRevocationUnavailable(RuntimeError):
    pass


class RedisRevocationClient(Protocol):
    async def set(self, key: str, value: str, ex: int) -> bool | str | bytes | None: ...

    async def exists(self, key: str) -> int: ...


_redis_client: RedisRevocationClient | None = None


def _key(token_hash: str) -> str:
    if not TOKEN_HASH_PATTERN.fullmatch(token_hash):
        raise ValueError("token_hash must be a lowercase SHA-256 hex digest")
    return f"{KEY_PREFIX}{token_hash}"


def _client() -> RedisRevocationClient:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_async.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis_client


def _normalized_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def revoke_admin_session_hash(
    token_hash: str,
    *,
    expires_at: datetime,
    redis_client: RedisRevocationClient | None = None,
    now: datetime | None = None,
) -> int:
    current_time = _normalized_datetime(now or datetime.now(UTC))
    expires_at_utc = _normalized_datetime(expires_at)
    ttl_seconds = max(0, math.ceil((expires_at_utc - current_time).total_seconds()))
    if ttl_seconds <= 0:
        return 0

    try:
        await (redis_client or _client()).set(_key(token_hash), "1", ex=ttl_seconds)
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        raise AdminSessionRevocationUnavailable("redis unavailable") from exc
    return ttl_seconds


async def is_admin_session_hash_revoked(
    token_hash: str,
    *,
    redis_client: RedisRevocationClient | None = None,
) -> bool:
    try:
        return bool(await (redis_client or _client()).exists(_key(token_hash)))
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        raise AdminSessionRevocationUnavailable("redis unavailable") from exc
