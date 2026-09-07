"""Real-Redis checks for Lua receipt atomicity (opt-in via COST_GUARD_REDIS_URL)."""

import os
import uuid
from datetime import datetime

import pytest
import redis.asyncio as redis_async

from app.services import cost_guard


@pytest.mark.skipif(
    not os.getenv("COST_GUARD_REDIS_URL"), reason="COST_GUARD_REDIS_URL is not configured"
)
async def test_real_redis_receipt_boundary_duplicate_and_zero_kill_switch(monkeypatch):
    client = redis_async.from_url(os.environ["COST_GUARD_REDIS_URL"])
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
