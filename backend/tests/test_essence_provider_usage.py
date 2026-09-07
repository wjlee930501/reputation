import asyncio

import pytest

from app.services import cost_guard, essence_engine, provider_usage


def test_failed_essence_http_attempt_is_observed_with_source_context(monkeypatch) -> None:
    class FailingMessages:
        def create(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    class FailingClient:
        messages = FailingMessages()

    observed: list[dict] = []

    async def capture_provider_attempt(**kwargs):
        observed.append(kwargs)
        return True

    async def capture_provider_count(_category, *, count=1):
        assert count == 1

    monkeypatch.setattr(essence_engine, "_anthropic_client", lambda: FailingClient())
    monkeypatch.setattr(provider_usage, "record_attempt", capture_provider_attempt)
    monkeypatch.setattr(cost_guard, "record_provider_call", capture_provider_count)

    async def scenario() -> None:
        async with essence_engine.metered_llm_calls(
            "11111111-1111-1111-1111-111111111111",
            workflow="SOURCE_EVIDENCE_EXTRACT",
            run_id="22222222-2222-2222-2222-222222222222",
            item_id="33333333-3333-3333-3333-333333333333",
            attempt_id="durable-attempt",
        ):
            await asyncio.to_thread(
                essence_engine._call_anthropic_json,
                "system",
                "input",
                max_tokens=100,
                attempts=1,
            )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(scenario())

    assert len(observed) == 1
    event = observed[0]
    assert event["workflow"] == "SOURCE_EVIDENCE_EXTRACT"
    assert event["item_id"] == "33333333-3333-3333-3333-333333333333"
    assert event["attempt_id"] == "durable-attempt"
    assert event["logical_call_id"] == "durable-attempt:call:1"
    assert event["http_attempt"] == 1
    assert event["usage_known"] is False
    assert event["provider_request_id"] is None
