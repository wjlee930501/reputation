"""Pins the sentinel for the app's own URLs (DATABASE_URL, SYNC_DATABASE_URL, REDIS_URL).

tests/conftest.py puts no default into os.environ. When a variable is unset, it points
the settings field at a non-routable sentinel, and any SQLAlchemy/redis-py connection
to that sentinel fails the test naming the variable — even through the app's
`except Exception` fallbacks. No database or Redis needed: the guard fires before any
network I/O.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
import redis
import redis.asyncio as redis_async
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from tests.db_env import (
    UNSET_APP_URL_SENTINELS,
    app_database_url_problem,
    drain_unset_url_uses,
    point_unset_app_urls_at_sentinels,
)


class _Outcome:
    message = ""
    swallowed = False
    used: list[str] = []


def _run_swallowing(call, outcome: _Outcome) -> None:
    """Call like app code that swallows ordinary errors (provider_usage, cost_guard)."""
    try:
        call()
    except Exception:  # noqa: BLE001 — the pattern the guard must survive
        outcome.swallowed = True


def _expect_unset_failure(call) -> _Outcome:
    outcome = _Outcome()
    try:
        _run_swallowing(call, outcome)
    except pytest.fail.Exception as exc:
        outcome.message = str(exc)
    finally:
        # Forget the recorded use so the autouse teardown check does not fail this test.
        outcome.used = drain_unset_url_uses()
    if not outcome.message:
        pytest.fail(f"sentinel connection did not fail (swallowed={outcome.swallowed})")
    return outcome


async def _expect_unset_failure_async(make_coro) -> _Outcome:
    outcome = _Outcome()
    try:
        try:
            await make_coro()
        except Exception:  # noqa: BLE001 — the pattern the guard must survive
            outcome.swallowed = True
    except pytest.fail.Exception as exc:
        outcome.message = str(exc)
    finally:
        outcome.used = drain_unset_url_uses()
    if not outcome.message:
        pytest.fail(f"sentinel connection did not fail (swallowed={outcome.swallowed})")
    return outcome


@pytest.mark.parametrize("name", sorted(UNSET_APP_URL_SENTINELS))
def test_settings_follow_the_env_or_the_sentinel(name: str) -> None:
    value = os.environ.get(name)

    assert getattr(settings, name) == (value or UNSET_APP_URL_SENTINELS[name])


def test_sentinels_are_only_put_into_settings_never_into_the_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SYNC_DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "redis://explicit.invalid:6379/1")
    fake = SimpleNamespace(DATABASE_URL="", SYNC_DATABASE_URL="", REDIS_URL="kept")

    unset = point_unset_app_urls_at_sentinels(fake)

    assert unset == ["DATABASE_URL", "SYNC_DATABASE_URL"]
    assert fake.DATABASE_URL == UNSET_APP_URL_SENTINELS["DATABASE_URL"]
    assert fake.SYNC_DATABASE_URL == UNSET_APP_URL_SENTINELS["SYNC_DATABASE_URL"]
    assert fake.REDIS_URL == "kept"
    assert "DATABASE_URL" not in os.environ
    assert os.environ["SYNC_DATABASE_URL"] == ""


@pytest.mark.parametrize("name", ["DATABASE_URL", "SYNC_DATABASE_URL"])
def test_session_refuses_an_app_database_that_is_not_a_test_database(name: str) -> None:
    """`make test`'s .env points the app at the dev database `reputation`."""
    problem = app_database_url_problem(
        {name: "postgresql+psycopg2://reputation:secret-pw@db:5432/reputation"}
    )

    assert problem is not None
    assert problem.startswith(f"{name} names database 'reputation'")
    assert "secret-pw" not in problem


def test_session_accepts_unset_or_test_app_databases() -> None:
    assert app_database_url_problem({}) is None
    assert app_database_url_problem({"DATABASE_URL": "", "SYNC_DATABASE_URL": ""}) is None
    assert (
        app_database_url_problem(
            {
                "DATABASE_URL": "postgresql+asyncpg://u:p@db.invalid/reputation_test",
                "SYNC_DATABASE_URL": "postgresql+psycopg2://u:p@db.invalid/reputation_release_test",
            }
        )
        is None
    )


def test_sync_engine_on_the_sentinel_fails_naming_the_variable() -> None:
    engine = sa.create_engine(UNSET_APP_URL_SENTINELS["SYNC_DATABASE_URL"])
    try:
        outcome = _expect_unset_failure(lambda: engine.connect().close())
    finally:
        engine.dispose()

    assert outcome.message.startswith("SYNC_DATABASE_URL is not set")
    assert outcome.used == ["SYNC_DATABASE_URL"]
    assert not outcome.swallowed


async def test_async_engine_on_the_sentinel_fails_naming_the_variable() -> None:
    engine = create_async_engine(UNSET_APP_URL_SENTINELS["DATABASE_URL"])

    async def connect() -> None:
        async with engine.connect():
            pass

    try:
        outcome = await _expect_unset_failure_async(connect)
    finally:
        await engine.dispose()

    assert outcome.message.startswith("DATABASE_URL is not set")
    assert outcome.used == ["DATABASE_URL"]
    assert not outcome.swallowed


def test_sync_redis_on_the_sentinel_fails_naming_the_variable() -> None:
    client = redis.Redis.from_url(UNSET_APP_URL_SENTINELS["REDIS_URL"])
    try:
        outcome = _expect_unset_failure(client.ping)
    finally:
        client.close()

    assert outcome.message.startswith("REDIS_URL is not set")
    assert outcome.used == ["REDIS_URL"]
    assert not outcome.swallowed


async def test_async_redis_on_the_sentinel_fails_naming_the_variable() -> None:
    client = redis_async.from_url(UNSET_APP_URL_SENTINELS["REDIS_URL"])
    try:
        outcome = await _expect_unset_failure_async(client.ping)
    finally:
        await client.aclose()

    assert outcome.message.startswith("REDIS_URL is not set")
    assert outcome.used == ["REDIS_URL"]
    assert not outcome.swallowed
