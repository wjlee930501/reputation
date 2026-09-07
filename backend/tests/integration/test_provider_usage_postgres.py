"""Real-Postgres contract for normalized provider-attempt usage."""

import uuid

import pytest
from sqlalchemy import select

from app.core import database
from app.models.usage import ProviderUsageEvent
from app.services import provider_usage


@pytest.fixture(autouse=True)
async def _dispose_usage_engine_after_test():
    yield
    if database.engine is not None:
        await database.engine.dispose()
    database.engine = None
    database.AsyncSessionLocal = None


async def test_provider_attempt_is_durable_and_idempotent_without_domain_transaction(pg_engine):
    idempotency_key = f"provider-usage-integration:{uuid.uuid4()}"
    try:
        first = await provider_usage.record_attempt(
            provider="openai",
            model="gpt-test",
            workflow="SOV_JUDGMENT",
            cost_category="leadgen",
            logical_call_id="logical-1",
            http_attempt=1,
            usage={"input_tokens": 0, "output_tokens": 0},
            idempotency_key=idempotency_key,
        )
        duplicate = await provider_usage.record_attempt(
            provider="openai",
            model="gpt-test",
            workflow="SOV_JUDGMENT",
            cost_category="leadgen",
            logical_call_id="logical-1",
            http_attempt=1,
            usage={"input_tokens": 0, "output_tokens": 0},
            idempotency_key=idempotency_key,
        )
        assert first is True and duplicate is True

        with pg_engine.connect() as conn:
            rows = conn.execute(
                select(
                    ProviderUsageEvent.usage_known,
                    ProviderUsageEvent.input_tokens,
                    ProviderUsageEvent.output_tokens,
                ).where(
                    ProviderUsageEvent.idempotency_key == idempotency_key
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].usage_known is True
        assert rows[0].input_tokens == rows[0].output_tokens == 0
    finally:
        with pg_engine.begin() as conn:
            conn.execute(
                ProviderUsageEvent.__table__.delete().where(
                    ProviderUsageEvent.idempotency_key == idempotency_key
                )
            )


async def test_telemetry_fk_failure_does_not_poison_callers_domain_session(
    pg_async_session, monkeypatch
):
    deferred = []

    async def capture_defer(event):
        deferred.append(event)
        return True

    monkeypatch.setattr(provider_usage, "_defer", capture_defer)
    recorded = await provider_usage.record_attempt(
        provider="openai",
        model="gpt-test",
        workflow="LEAD_JUDGMENT",
        cost_category="leadgen",
        lead_id=uuid.uuid4(),  # deliberately absent: telemetry insert must fail its own txn
        usage_known=False,
        db=pg_async_session,
    )

    assert recorded is False
    assert len(deferred) == 1
    assert (await pg_async_session.execute(select(1))).scalar_one() == 1
