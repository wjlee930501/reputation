import asyncio
import logging
import math
import re
from datetime import UTC, datetime
from typing import Protocol

import redis.asyncio as redis_async
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

TOKEN_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
KEY_PREFIX = "admin-session-revoked:"


class AdminSessionRevocationUnavailable(RuntimeError):
    pass


class RedisRevocationClient(Protocol):
    async def set(self, key: str, value: str, ex: int) -> bool | str | bytes | None: ...

    async def exists(self, key: str) -> int: ...


_redis_client: RedisRevocationClient | None = None
_CONNECT_TIMEOUT_SECONDS = 0.5
_COMMAND_TIMEOUT_SECONDS = 0.7
_CALL_DEADLINE_SECONDS = 2.5


def _key(token_hash: str) -> str:
    if not TOKEN_HASH_PATTERN.fullmatch(token_hash):
        raise ValueError("token_hash must be a lowercase SHA-256 hex digest")
    return f"{KEY_PREFIX}{token_hash}"


def _client() -> RedisRevocationClient:
    global _redis_client
    if _redis_client is None:
        # 유휴 중 네트워크가 끊어 둔 풀 커넥션은 다음 명령에서 곧바로 reset된다. from_url의
        # 기본 재시도는 0회라 그 요청만 확인 불가(503)가 된다. 연결 오류에 한해 새 커넥션으로
        # 한 번 더 묻는다. Redis가 실제로 죽었으면 재시도도 실패하고 종전처럼 닫힌다.
        # 새 연결마다 보내는 CLIENT SETINFO 2건은 응답을 각각 socket_timeout까지 기다려
        # 최악을 늘린다. 쓰지 않는 정보이므로 보내지 않고, 호출 전체는 _CALL_DEADLINE_SECONDS로
        # 묶는다(BFF 폐기 확인 예산 3초보다 먼저 끝나야 BFF가 끊기 전에 503으로 닫힌다).
        _redis_client = redis_async.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=_COMMAND_TIMEOUT_SECONDS,
            retry=Retry(NoBackoff(), 1, supported_errors=(RedisConnectionError,)),
            lib_name=None,
            lib_version=None,
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
        async with asyncio.timeout(_CALL_DEADLINE_SECONDS):
            await (redis_client or _client()).set(_key(token_hash), "1", ex=ttl_seconds)
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        logger.warning("admin session revocation store unavailable: %s", type(exc).__name__)
        raise AdminSessionRevocationUnavailable("redis unavailable") from exc
    return ttl_seconds


async def is_admin_session_hash_revoked(
    token_hash: str,
    *,
    redis_client: RedisRevocationClient | None = None,
) -> bool:
    try:
        async with asyncio.timeout(_CALL_DEADLINE_SECONDS):
            return bool(await (redis_client or _client()).exists(_key(token_hash)))
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        logger.warning("admin session revocation store unavailable: %s", type(exc).__name__)
        raise AdminSessionRevocationUnavailable("redis unavailable") from exc
