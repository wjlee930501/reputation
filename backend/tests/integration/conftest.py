"""Real-Postgres integration fixtures (TEST-1).

These tests exercise actual SQL: unique constraints, the audit append-only trigger,
NOT NULL columns, covering indexes, and the public API's cross-tenant predicates —
paths the mock-based unit suite cannot reach.

Availability policy: `INTEGRATION_DATABASE_URL` is **required** and has no default.
Unset → the tests that need the database fail, naming the variable. Set but
unreachable or unmigrated → hard failure too. Without this, a missing or degraded
Postgres silently skips the only tests in the repo that run real SQL and the build
still goes green.

Point INTEGRATION_DATABASE_URL at a migrated test DB. The URL is read when a fixture
runs, not at import, so collecting this directory never errors the session.
"""
import pytest

from tests.db_env import require_db_url

_URL_ENV = "INTEGRATION_DATABASE_URL"


def integration_url() -> str:
    """INTEGRATION_DATABASE_URL, or fail the calling test when it is unset."""
    return require_db_url(_URL_ENV)


def _unavailable(reason: str):
    """The integration DB is always required: fail loudly, never skip."""
    pytest.fail(
        f"{_URL_ENV} is set, so the integration Postgres is required "
        f"and must not be skipped: {reason}",
        pytrace=False,
    )


def _require(module: str):
    """importorskip, but a hard failure: the integration DB is always required."""
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - depends on the environment
        _unavailable(f"{module} is not installed: {exc}")


def _async_url(url: str) -> str:
    """psycopg2/plain DSN → asyncpg DSN (the app's public API is async-only)."""
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


@pytest.fixture(scope="session")
def pg_engine():
    url = integration_url()
    sqlalchemy = _require("sqlalchemy")
    _require("psycopg2")
    engine = sqlalchemy.create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        _unavailable(f"No integration Postgres at {url}: {exc.__class__.__name__}: {exc}")
    return engine


def _assert_migrated(conn) -> None:
    from sqlalchemy import text

    has_trigger = conn.execute(
        text("SELECT 1 FROM pg_trigger WHERE tgname = 'admin_audit_logs_block_mutation'")
    ).first()
    if not has_trigger:
        _unavailable("Integration DB not migrated to head (run `alembic upgrade head`).")


@pytest.fixture
def pg_conn(pg_engine):
    """A connection wrapped in a transaction that is rolled back after each test."""
    conn = pg_engine.connect()
    trans = conn.begin()
    # Ensure the schema is migrated (the append-only trigger lives in a migration,
    # not in metadata) — fail loudly if the test DB was never upgraded.
    try:
        _assert_migrated(conn)
    except BaseException:
        trans.rollback()
        conn.close()
        raise
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


@pytest.fixture
async def pg_async_session():
    """AsyncSession on the integration Postgres, rolled back after each test.

    The public API (`app.api.public.site`) is async-only, so the cross-tenant
    isolation tests need a real async connection rather than the psycopg2 one.
    """
    url = integration_url()
    _require("asyncpg")
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine(_async_url(url), future=True)
    try:
        try:
            conn = await engine.connect()
        except Exception as exc:  # noqa: BLE001
            _unavailable(f"No integration Postgres at {url}: {exc.__class__.__name__}: {exc}")
        trans = await conn.begin()
        try:
            await conn.run_sync(_assert_migrated)
            session = AsyncSession(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            try:
                yield session
            finally:
                await session.close()
        finally:
            await trans.rollback()
            await conn.close()
    finally:
        await engine.dispose()
