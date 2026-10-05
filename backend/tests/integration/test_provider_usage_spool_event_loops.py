"""Provider usage spool and replay across event loops, on real Postgres and real Redis.

The worker's `drain` runs `asyncio.run(...)` every minute, so each run is a new event
loop in the same process. A Redis client cached in a module global keeps a pooled
connection bound to the loop that created it: the next loop's call fails with
`RuntimeError: Event loop is closed`, the client drops the connection, and the call
after that works. Beat dispatches the drain every minute, so replay was skipped every
other minute ("provider usage recovery replay skipped: redis unavailable", ~660/day).
The same client also spools `record_attempt` after a DB failure (`_defer`), so a
spool attempt from a second loop was lost together with the usage record.

The loop tests run each step in its own `asyncio.run`, like the worker. The async
engine is built as the worker builds it (`SERVICE=worker` → NullPool), so a database
connection never crosses loops and only the Redis path is under test. The semantic
tests (idempotent replay, failed persist stays spooled, Redis error mid-replay) inject
`redis_client=` and run in one loop; they hold the replay contract while the client
handling changes.

Isolation: Redis is `INTEGRATION_REDIS_URL` with a per-test spool index key; every
event key and DB row a test creates is removed in teardown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

import pytest
import redis
import redis.asyncio as redis_async
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import delete, func, select

from app.core import database
from app.core.config import settings
from app.models.hospital import Hospital, HospitalStatus
from app.models.usage import HospitalUsageEvent, ProviderUsageEvent
from app.services import provider_usage
from app.workers import provider_usage_recovery
from tests.db_env import require_redis_url

_REDIS_ENV = "INTEGRATION_REDIS_URL"
_LOGGER = "app.services.provider_usage"
_REDIS_WARNINGS = ("redis unavailable", "spool unavailable")


def _database_down():
    raise ConnectionRefusedError("provider usage test: database unavailable")


@pytest.fixture
def redis_url():
    return require_redis_url(_REDIS_ENV)


@pytest.fixture
def spool(redis_url, monkeypatch):
    """Point the spool at the integration Redis under a per-test index key."""
    index_key = f"provider_usage:recovery:index:test:{uuid.uuid4().hex}"
    monkeypatch.setattr(settings, "REDIS_URL", redis_url)
    monkeypatch.setattr(provider_usage, "_RECOVERY_INDEX_KEY", index_key)
    # A client cached by an earlier test may point at another Redis/loop.
    monkeypatch.setattr(provider_usage, "_recovery_redis", None, raising=False)
    client = redis.Redis.from_url(redis_url)
    try:
        client.ping()
    except redis.RedisError as exc:  # pragma: no cover - depends on the environment
        pytest.fail(
            f"{_REDIS_ENV} is set but its Redis is unreachable ({type(exc).__name__}).",
            pytrace=False,
        )
    try:
        yield client, index_key
    finally:
        for raw_key in client.zrange(index_key, 0, -1):
            client.delete(raw_key)
        client.delete(index_key)
        client.close()


@pytest.fixture
def worker_engine(monkeypatch):
    """Build the app async engine the way Worker/Beat do (NullPool per loop)."""
    monkeypatch.setenv("SERVICE", "worker")
    database.engine = None
    database.AsyncSessionLocal = None
    yield
    engine = database.engine
    database.engine = None
    database.AsyncSessionLocal = None
    if engine is not None:
        asyncio.run(engine.dispose())


@pytest.fixture
def hospital(pg_engine):
    """A committed hospital: `_persist` writes through its own session."""
    hospital_id = uuid.uuid4()
    with pg_engine.begin() as conn:
        conn.execute(
            Hospital.__table__.insert().values(
                id=hospital_id,
                name="사용량 복구 가상의원",
                slug=f"usage-spool-{hospital_id.hex[:10]}",
                status=HospitalStatus.ACTIVE.value,
            )
        )
    try:
        yield hospital_id
    finally:
        with pg_engine.begin() as conn:
            conn.execute(
                delete(HospitalUsageEvent).where(HospitalUsageEvent.hospital_id == hospital_id)
            )
            conn.execute(
                delete(ProviderUsageEvent).where(ProviderUsageEvent.hospital_id == hospital_id)
            )
            conn.execute(Hospital.__table__.delete().where(Hospital.id == hospital_id))


@pytest.fixture
def key_prefix(pg_engine):
    prefix = f"provider-usage-spool-test:{uuid.uuid4().hex}"
    try:
        yield prefix
    finally:
        with pg_engine.begin() as conn:
            conn.execute(
                delete(ProviderUsageEvent).where(
                    ProviderUsageEvent.idempotency_key.like(f"{prefix}%")
                )
            )


async def _record(idempotency_key: str, hospital_id: uuid.UUID | None = None) -> bool:
    return await provider_usage.record_attempt(
        provider="anthropic",
        model="claude-test",
        workflow="CONTENT_GENERATION",
        cost_category="content" if hospital_id else "leadgen",
        hospital_id=hospital_id,
        usage={"input_tokens": 11, "output_tokens": 7},
        idempotency_key=idempotency_key,
    )


def _record_with_database_down(monkeypatch, idempotency_key, hospital_id=None) -> bool:
    with monkeypatch.context() as patch:
        patch.setattr(provider_usage, "get_async_sessionmaker", _database_down)
        return asyncio.run(_record(idempotency_key, hospital_id))


def _spooled_keys(client, index_key) -> dict[str, float]:
    """idempotency_key → index score, for every event payload still in the spool."""
    spooled = {}
    for raw_key, score in client.zrange(index_key, 0, -1, withscores=True):
        payload = client.get(raw_key)
        assert payload is not None, f"index entry {raw_key!r} lost its payload"
        spooled[json.loads(payload)["idempotency_key"]] = score
    return spooled


def _persisted_count(pg_engine, idempotency_key) -> int:
    with pg_engine.connect() as conn:
        return int(
            conn.scalar(
                select(func.count())
                .select_from(ProviderUsageEvent)
                .where(ProviderUsageEvent.idempotency_key == idempotency_key)
            )
        )


def _legacy_count(pg_engine, hospital_id) -> int:
    with pg_engine.connect() as conn:
        return int(
            conn.scalar(
                select(func.count())
                .select_from(HospitalUsageEvent)
                .where(HospitalUsageEvent.hospital_id == hospital_id)
            )
        )


def _redis_warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _LOGGER
        and any(marker in record.getMessage() for marker in _REDIS_WARNINGS)
    ]


def _drain(monkeypatch) -> dict[str, int]:
    monkeypatch.setattr(provider_usage_recovery, "require_dispatch", lambda *_args: None)
    return provider_usage_recovery.drain.run()


# ── Event loops: RED on the module-global client ─────────────────────────────────────


def test_db_failure_spools_every_attempt_from_separate_event_loops(
    spool, key_prefix, monkeypatch, caplog
):
    client, index_key = spool
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    keys = [f"{key_prefix}:{index}" for index in range(3)]

    for key in keys:
        # Telemetry stays observational: the provider workflow is told "not persisted".
        assert _record_with_database_down(monkeypatch, key) is False

    # Every attempt must reach the spool, or its cost record is gone for good.
    assert sorted(_spooled_keys(client, index_key)) == sorted(keys)
    assert _redis_warnings(caplog) == []


def test_drain_replays_in_every_new_event_loop_and_persists_each_attempt_once(
    spool, worker_engine, hospital, pg_engine, monkeypatch, caplog
):
    client, index_key = spool
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    prefix = f"provider-usage-drain-test:{uuid.uuid4().hex}"
    keys = [f"{prefix}:{index}" for index in range(3)]

    results = []
    for key in keys:
        assert _record_with_database_down(monkeypatch, key, hospital) is False
        # Beat dispatches one drain per minute; each runs in a fresh event loop.
        results.append(_drain(monkeypatch))

    assert results == [{"recovered": 1, "missing": 0, "failed": 0}] * 3
    assert _redis_warnings(caplog) == []
    assert [_persisted_count(pg_engine, key) for key in keys] == [1, 1, 1]
    # The legacy hospital aggregate is written once per replayed content attempt.
    assert _legacy_count(pg_engine, hospital) == 3
    assert _spooled_keys(client, index_key) == {}

    # A later, empty drain in yet another loop still reaches Redis.
    assert _drain(monkeypatch) == {"recovered": 0, "missing": 0, "failed": 0}
    assert _redis_warnings(caplog) == []


def test_replay_after_a_spool_from_another_loop_does_not_lose_or_duplicate(
    spool, worker_engine, key_prefix, pg_engine, monkeypatch, caplog
):
    client, index_key = spool
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    key = f"{key_prefix}:cross-loop"

    assert _record_with_database_down(monkeypatch, key) is False
    first = asyncio.run(provider_usage.replay_deferred_attempts())
    second = asyncio.run(provider_usage.replay_deferred_attempts())

    assert first == {"recovered": 1, "missing": 0, "failed": 0}
    assert second == {"recovered": 0, "missing": 0, "failed": 0}
    assert _persisted_count(pg_engine, key) == 1
    assert _spooled_keys(client, index_key) == {}
    assert _redis_warnings(caplog) == []


# ── Replay semantics with an injected client: GREEN preservation ─────────────────────


@pytest.fixture
async def injected(redis_url):
    client = redis_async.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        yield client
    finally:
        await client.aclose()


async def test_replaying_an_already_persisted_attempt_removes_it_without_duplicating(
    spool, injected, key_prefix, pg_engine, monkeypatch
):
    client, index_key = spool
    key = f"{key_prefix}:already-persisted"
    with monkeypatch.context() as patch:
        patch.setattr(provider_usage, "get_async_sessionmaker", _database_down)
        assert await _record(key) is False
    assert list(_spooled_keys(client, index_key)) == [key]
    # The same attempt is recorded again while the DB is healthy (same idempotency key).
    assert await _record(key) is True

    result = await provider_usage.replay_deferred_attempts(redis_client=injected)

    assert result == {"recovered": 1, "missing": 0, "failed": 0}
    assert _persisted_count(pg_engine, key) == 1
    assert _spooled_keys(client, index_key) == {}
    # An injected client belongs to the caller: replay must not close it.
    assert await injected.ping() is True


async def test_a_failed_persist_during_replay_keeps_the_attempt_spooled(
    spool, injected, key_prefix, pg_engine, monkeypatch
):
    client, index_key = spool
    key = f"{key_prefix}:still-down"
    with monkeypatch.context() as patch:
        patch.setattr(provider_usage, "get_async_sessionmaker", _database_down)
        assert await _record(key) is False
        before = time.time()
        result = await provider_usage.replay_deferred_attempts(redis_client=injected)

    assert result == {"recovered": 0, "missing": 0, "failed": 1}
    spooled = _spooled_keys(client, index_key)
    assert list(spooled) == [key]
    # Rescheduled into the future, not deleted.
    assert spooled[key] >= before + 30
    assert _persisted_count(pg_engine, key) == 0

    # Once due again and the DB is back, it is persisted exactly once.
    client.zadd(index_key, {client.zrange(index_key, 0, -1)[0]: 0})
    assert await provider_usage.replay_deferred_attempts(redis_client=injected) == {
        "recovered": 1,
        "missing": 0,
        "failed": 0,
    }
    assert _persisted_count(pg_engine, key) == 1
    assert _spooled_keys(client, index_key) == {}


class _FailFirstDelete:
    """The real client, but the first payload delete raises a Redis connection error."""

    def __init__(self, client):
        self._client = client
        self.failed = False

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def delete(self, *keys):
        if not self.failed:
            self.failed = True
            raise RedisConnectionError("provider usage test: redis dropped mid-replay")
        return await self._client.delete(*keys)


async def test_a_redis_error_mid_replay_never_deletes_an_unpersisted_attempt(
    spool, injected, key_prefix, pg_engine, monkeypatch, caplog
):
    client, index_key = spool
    keys = [f"{key_prefix}:mid-replay:{index}" for index in range(2)]
    with monkeypatch.context() as patch:
        patch.setattr(provider_usage, "get_async_sessionmaker", _database_down)
        for key in keys:
            assert await _record(key) is False
    # Replay order is by score; pin it so "first" and "second" are deterministic.
    by_key = {json.loads(client.get(raw))["idempotency_key"]: raw for raw in client.zrange(index_key, 0, -1)}
    client.zadd(index_key, {by_key[key]: score for score, key in enumerate(keys)})

    flaky = _FailFirstDelete(injected)
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    interrupted = await provider_usage.replay_deferred_attempts(redis_client=flaky)

    assert interrupted == {"recovered": 0, "missing": 0, "failed": 1}
    # The first attempt reached the DB but could not be unspooled; the second was never
    # attempted. Neither may disappear from the spool.
    assert sorted(_spooled_keys(client, index_key)) == sorted(keys)
    assert [_persisted_count(pg_engine, key) for key in keys] == [1, 0]

    healed = await provider_usage.replay_deferred_attempts(redis_client=injected)

    assert healed == {"recovered": 2, "missing": 0, "failed": 0}
    assert [_persisted_count(pg_engine, key) for key in keys] == [1, 1]
    assert _spooled_keys(client, index_key) == {}
