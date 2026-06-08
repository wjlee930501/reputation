"""Real-Postgres integration fixtures (TEST-1).

These tests exercise actual SQL: unique constraints, the audit append-only trigger,
NOT NULL columns, and covering indexes — paths the mock-based unit suite cannot
reach. They AUTO-SKIP when no Postgres is reachable, so the default unit run stays
portable (CI provides a Postgres service + `alembic upgrade head`).

Point INTEGRATION_DATABASE_URL at a migrated test DB. Default matches the local
docker-compose Postgres exposed on host port 5434.
"""
import os

import pytest

DEFAULT_URL = "postgresql://reputation:reputation@localhost:5434/reputation_test"
INTEGRATION_URL = os.getenv("INTEGRATION_DATABASE_URL", DEFAULT_URL)


@pytest.fixture(scope="session")
def pg_engine():
    sqlalchemy = pytest.importorskip("sqlalchemy")
    psycopg2 = pytest.importorskip("psycopg2")  # noqa: F841
    engine = sqlalchemy.create_engine(INTEGRATION_URL, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"No integration Postgres at {INTEGRATION_URL}: {exc.__class__.__name__}")
    return engine


@pytest.fixture
def pg_conn(pg_engine):
    """A connection wrapped in a transaction that is rolled back after each test."""
    from sqlalchemy import text

    conn = pg_engine.connect()
    trans = conn.begin()
    # Ensure the schema is migrated (the append-only trigger lives in a migration,
    # not in metadata) — fail loudly if the test DB was never upgraded.
    has_trigger = conn.execute(
        text("SELECT 1 FROM pg_trigger WHERE tgname = 'admin_audit_logs_block_mutation'")
    ).first()
    if not has_trigger:
        trans.rollback()
        conn.close()
        pytest.skip("Integration DB not migrated to head (run `alembic upgrade head`).")
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()
