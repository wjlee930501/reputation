"""Test database/Redis URLs come only from the environment — there is no localhost default.

A DB or Redis test whose URL env var is missing fails with the variable's name instead
of silently connecting to (or skipping on) whatever server happens to be on a default
port. Look the URL up at fixture/test time, never at import time, so that collecting a
module with a missing variable does not break the session or the module's non-DB tests.

A set URL whose database refuses the connection fails too (`fail_unreachable`): a skip
there would let CI go green while its Postgres service is down. A set DB URL must also
name a test database (`*_test`, or one of `_DEDICATED_TEST_DATABASES`), so a
misconfigured URL cannot run committed cleanup DELETEs against a development database.

The app's own URLs (DATABASE_URL, SYNC_DATABASE_URL, REDIS_URL) are read by
`app.core.config.settings`, not by tests. When one is unset, tests/conftest.py points
the settings field at a non-routable sentinel host (never writing os.environ), and
`install_unset_url_guards` makes any connection to a sentinel fail the test naming the
variable — even where app code catches `Exception` and would otherwise pass silently.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, NoReturn

import pytest

_URL_HELP = (
    "has no localhost default; export it (see the backend job env in "
    ".github/workflows/ci.yml for the expected values)."
)
# Dedicated test databases whose names do not end in `_test`:
# MIGRATION_UPGRADE_DATABASE_URL's (created by a CI step) and the isolated auxiliary
# database scripts/verify_release_candidate.py points the per-test URLs at.
_DEDICATED_TEST_DATABASES = frozenset({"reputation_autonomy_migration", "reputation_release_aux"})


def _missing_message(names: list[str]) -> str:
    verb = "is" if len(names) == 1 else "are"
    return (
        f"{' / '.join(names)} {verb} not set. This test needs an explicit test URL "
        f"and {_URL_HELP}"
    )


def _first_set(env_names: tuple[str, ...], caller: str) -> tuple[str, str]:
    if not env_names:
        raise TypeError(f"{caller}() needs at least one env var name")
    for name in env_names:
        value = os.environ.get(name)
        if value:
            return name, value
    pytest.fail(_missing_message(list(env_names)), pytrace=False)


def is_test_database_name(database: str | None) -> bool:
    return bool(database) and (
        database.endswith("_test") or database in _DEDICATED_TEST_DATABASES
    )


def _require_test_database(env_name: str, url: str) -> str:
    """Fail unless `url` names a test database. The URL is not echoed (password)."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        database = make_url(url).database
    except ArgumentError as exc:
        pytest.fail(f"{env_name} is not a valid database URL ({type(exc).__name__}).", pytrace=False)
    if not is_test_database_name(database):
        pytest.fail(
            f"{env_name} names database {database!r}, not a test database. A test DB URL "
            "must name a database ending in '_test' (or one of the dedicated "
            f"{sorted(_DEDICATED_TEST_DATABASES)}), so tests never write to a dev database.",
            pytrace=False,
        )
    return url


def require_db_url(*env_names: str) -> str:
    """Return the first non-empty env var of `env_names`, else fail the calling test.

    The URL must name a test database (see `is_test_database_name`).
    """
    name, value = _first_set(env_names, "require_db_url")
    return _require_test_database(name, value)


def require_db_urls(*env_names: str) -> tuple[str, ...]:
    """Return every env var of `env_names`; fail naming all that are missing or empty."""
    if not env_names:
        raise TypeError("require_db_urls() needs at least one env var name")
    missing = [name for name in env_names if not os.environ.get(name)]
    if missing:
        pytest.fail(_missing_message(missing), pytrace=False)
    return tuple(_require_test_database(name, os.environ[name]) for name in env_names)


def require_redis_url(*env_names: str) -> str:
    """Return the first non-empty Redis URL env var of `env_names`, else fail the test."""
    return _first_set(env_names, "require_redis_url")[1]


def fail_unreachable(env_name: str, exc: BaseException) -> NoReturn:
    """Fail the calling test: `env_name` is set but its database could not be connected to.

    Call it from the except block of the connection attempt. The URL is not echoed
    because it may carry a password.
    """
    pytest.fail(
        f"{env_name} is set but its PostgreSQL is unreachable ({type(exc).__name__}). "
        "A set test DB URL must connect; this fails instead of skipping.",
        pytrace=False,
    )


# ── The app's own URLs: sentinel instead of a default ───────────────────────────────
# `.invalid` never resolves (RFC 6761) and the guards below fail before any lookup.
# Hosts are lowercase because urllib lowercases hostnames (redis.from_url uses it).
UNSET_APP_URL_SENTINELS: dict[str, str] = {
    "DATABASE_URL": "postgresql+asyncpg://database-url-is-not-set.invalid/unset_test",
    "SYNC_DATABASE_URL": "postgresql+psycopg2://sync-database-url-is-not-set.invalid/unset_test",
    "REDIS_URL": "redis://redis-url-is-not-set.invalid:6379/0",
}
_SENTINEL_HOSTS = {
    "database-url-is-not-set.invalid": "DATABASE_URL",
    "sync-database-url-is-not-set.invalid": "SYNC_DATABASE_URL",
    "redis-url-is-not-set.invalid": "REDIS_URL",
}
_unset_url_uses: list[str] = []
_guards_installed = False


def app_database_url_problem(environ: Mapping[str, str]) -> str | None:
    """Why the set DATABASE_URL/SYNC_DATABASE_URL must not run the suite, else None.

    Both must name a database ending in `_test`, so `make test`'s .env or a developer
    shell can never point the app at the dev database `reputation`. No password echo.
    """
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    for name in ("DATABASE_URL", "SYNC_DATABASE_URL"):
        value = environ.get(name)
        if not value:
            continue
        try:
            database = make_url(value).database
        except ArgumentError as exc:
            return f"{name} is not a valid database URL ({type(exc).__name__})."
        if not (database or "").endswith("_test"):
            return (
                f"{name} names database {database!r}; the test suite only runs against a "
                "database whose name ends in '_test' (see .github/workflows/ci.yml)."
            )
    return None


def unset_app_url_message(name: str) -> str:
    return (
        f"{name} is not set. The code under test connected through settings.{name}, "
        f"which in tests {_URL_HELP}"
    )


def point_unset_app_urls_at_sentinels(settings: Any) -> list[str]:
    """Point each unset/empty app URL setting at its sentinel; return the names.

    os.environ is left untouched, so `require_db_url("SYNC_DATABASE_URL")` still
    reports the variable as unset.
    """
    unset = [name for name in UNSET_APP_URL_SENTINELS if not os.environ.get(name)]
    for name in unset:
        setattr(settings, name, UNSET_APP_URL_SENTINELS[name])
    return unset


def drain_unset_url_uses() -> list[str]:
    """Return (and forget) the variables whose sentinel was connected to so far."""
    used = list(dict.fromkeys(_unset_url_uses))
    _unset_url_uses.clear()
    return used


def _guard_host(host: object) -> None:
    name = _SENTINEL_HOSTS.get(str(host).lower()) if host else None
    if name is None:
        return
    _unset_url_uses.append(name)
    # Failed is a BaseException: an app-level `except Exception` cannot swallow it.
    pytest.fail(unset_app_url_message(name), pytrace=False)


def _guard_sqlalchemy_connect(dialect, conn_rec, cargs, cparams) -> None:
    _guard_host(cparams.get("host"))


def install_unset_url_guards() -> None:
    """Make every SQLAlchemy/redis-py connection to a sentinel host fail the test.

    Idempotent. The SQLAlchemy listener is class-level on Engine, which also covers
    the sync engine behind every AsyncEngine. redis-py routes both `connect()` and the
    lazy connect inside `send_packed_command` through `connect_check_health`, for the
    sync and asyncio clients alike (Celery/kombu's Redis transport included).
    """
    global _guards_installed
    if _guards_installed:
        return
    import redis.asyncio.connection as redis_async_connection
    import redis.connection as redis_connection
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    event.listen(Engine, "do_connect", _guard_sqlalchemy_connect)

    sync_original = redis_connection.AbstractConnection.connect_check_health
    async_original = redis_async_connection.AbstractConnection.connect_check_health

    def sync_connect_check_health(self, *args, **kwargs):
        _guard_host(getattr(self, "host", None))
        return sync_original(self, *args, **kwargs)

    async def async_connect_check_health(self, *args, **kwargs):
        _guard_host(getattr(self, "host", None))
        return await async_original(self, *args, **kwargs)

    redis_connection.AbstractConnection.connect_check_health = sync_connect_check_health
    redis_async_connection.AbstractConnection.connect_check_health = async_connect_check_health
    _guards_installed = True
