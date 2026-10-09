"""Real-Redis checks for Lua receipt atomicity (COST_GUARD_REDIS_URL is required)."""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
import redis.asyncio as redis_async
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services import cost_guard
from tests.db_env import require_db_url, require_redis_url


@pytest.fixture(autouse=True)
async def _rollback_durable_alerts(monkeypatch) -> AsyncIterator[None]:
    """Keep real durable alert commits inside the integration fixture transaction."""
    url = make_url(require_db_url("INTEGRATION_DATABASE_URL")).set(
        drivername="postgresql+asyncpg"
    )
    engine = create_async_engine(url)
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(cost_guard, "get_async_sessionmaker", lambda: factory)
    try:
        yield
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def test_real_redis_receipt_boundary_duplicate_and_zero_kill_switch(monkeypatch):
    client = redis_async.from_url(require_redis_url("COST_GUARD_REDIS_URL"))
    reservation_id = f"integration-{uuid.uuid4()}"
    before = datetime(2026, 8, 31, 23, 59, tzinfo=cost_guard._KST)
    daily_key = cost_guard._daily_key("content", "20260831")
    monthly_key = cost_guard._monthly_key("content", "202608")
    receipt_key = cost_guard._reservation_key(reservation_id)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_ENABLED", True)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_DAILY_CONTENT_CALLS", 100)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_MONTHLY_CONTENT_CALLS", 100)
    try:
        await client.delete(daily_key, monthly_key, receipt_key, cost_guard.KILL_SWITCH_KEY)
        first = await cost_guard.reserve(
            "content", count=5, reservation_id=reservation_id, reserved_at=before,
            redis_client=client,
        )
        duplicate = await cost_guard.reserve(
            "content", count=5, reservation_id=reservation_id,
            reserved_at=datetime(2026, 9, 1, 0, 1, tzinfo=cost_guard._KST),
            redis_client=client,
        )
        assert first.receipt == duplicate.receipt
        assert int(await client.get(daily_key)) == 5
        assert int(await client.get(monthly_key)) == 5

        settled = await cost_guard.settle_reservation(
            first.receipt, consumed_units=2, redis_client=client
        )
        settled_again = await cost_guard.settle_reservation(
            first.receipt, consumed_units=2, redis_client=client
        )
        assert settled == settled_again
        assert settled is not None
        assert (settled.consumed_units, settled.released_units) == (2, 3)
        assert int(await client.get(daily_key)) == 2
        assert int(await client.get(monthly_key)) == 2

        await cost_guard.set_kill_switch(True, redis_client=client)
        blocked = await cost_guard.reserve("leadgen", count=0, redis_client=client)
        assert blocked.allowed is False
    finally:
        await client.delete(daily_key, monthly_key, receipt_key, cost_guard.KILL_SWITCH_KEY)
        await client.aclose()


async def test_partial_budget_can_buy_one_stage_without_fitting_the_whole_remainder(monkeypatch):
    client = redis_async.from_url(require_redis_url("COST_GUARD_REDIS_URL"))
    now = datetime(2026, 10, 9, 12, 0, tzinfo=cost_guard._KST)
    daily_key = cost_guard._daily_key("sov", "20261009")
    monthly_key = cost_guard._monthly_key("sov", "202610")
    receipt_key = cost_guard._reservation_key("partial-budget-stage")
    daily_hard_alert_key = "cost_guard:sov:daily:hard_alerted:20261009"
    daily_soft_alert_key = "cost_guard:sov:daily:soft_alerted:20261009"
    monthly_hard_alert_key = "cost_guard:sov:monthly:hard_alerted:202610"
    monthly_soft_alert_key = "cost_guard:sov:monthly:soft_alerted:202610"
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_ENABLED", True)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_DAILY_SOV_QUERIES", 10)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_MONTHLY_SOV_QUERIES", 100)
    try:
        await client.delete(
            daily_key,
            monthly_key,
            receipt_key,
            daily_hard_alert_key,
            daily_soft_alert_key,
            monthly_hard_alert_key,
            monthly_soft_alert_key,
            cost_guard.KILL_SWITCH_KEY,
        )
        await client.set(daily_key, 9)
        decision = await cost_guard.reserve(
            "sov",
            count=1,
            reservation_id="partial-budget-stage",
            reserved_at=now,
            redis_client=client,
        )

        assert decision.allowed is True
        assert int(await client.get(daily_key)) == 10
        assert int(await client.get(monthly_key)) == 1
    finally:
        await client.delete(
            daily_key,
            monthly_key,
            receipt_key,
            daily_hard_alert_key,
            daily_soft_alert_key,
            monthly_hard_alert_key,
            monthly_soft_alert_key,
            cost_guard.KILL_SWITCH_KEY,
        )
        await client.aclose()
