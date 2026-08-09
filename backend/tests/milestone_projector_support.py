from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get(
    "TASK13_DATABASE_URL",
    "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test",
)
SLUG = "ops-qa-t13-monthly-ready"
NAME = "OPS-QA-T13-MONTHLY-READY"
ADMIN_ID = uuid.UUID("a1340000-0000-0000-0000-000000000001")
ADMIN_EMAIL = "task13-validator@example.invalid"


@pytest.fixture(name="monthly_sessions")
async def monthly_session_factory():
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.connect() as connection:
        has_current_schema = await connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' "
                "AND table_name='monthly_measurement_manifests' "
                "AND column_name='closes_at')"
            )
        )
    if not has_current_schema:
        await engine.dispose()
        pytest.skip(
            "real-PostgreSQL projector test requires a fresh Alembic-head schema; "
            "set TASK13_DATABASE_URL to an isolated migrated database"
        )

    async def cleanup() -> None:
        async with sessions() as db:
            await db.execute(
                text(
                    "DELETE FROM incidents WHERE hospital_id IN "
                    "(SELECT id FROM hospitals WHERE slug=:slug)"
                ),
                {"slug": SLUG},
            )
            await db.execute(
                text(
                    "DELETE FROM notification_outbox WHERE hospital_id IN "
                    "(SELECT id FROM hospitals WHERE slug=:slug)"
                ),
                {"slug": SLUG},
            )
            await db.execute(text("DELETE FROM hospitals WHERE slug=:slug"), {"slug": SLUG})
            await db.execute(
                text("DELETE FROM admin_users WHERE id=:admin_id"), {"admin_id": ADMIN_ID}
            )
            await db.execute(
                text("DELETE FROM operation_runs WHERE operation_type='MILESTONE_PROJECTION'")
            )
            await db.commit()

    await cleanup()
    try:
        yield sessions
    finally:
        await cleanup()
        await engine.dispose()
