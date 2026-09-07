import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services.cost_guard import CostGuardDecision
from app.workers import tasks


def _source():
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        raw_text="원문",
    )


def test_source_provider_boundary_reserves_then_settles_actual_calls(monkeypatch) -> None:
    source = _source()
    receipt = object()
    events: list[tuple] = []

    async def reserve(**kwargs):
        events.append(("reserve", kwargs["count"], kwargs["reservation_id"]))
        return CostGuardDecision(True, receipt=receipt)

    @asynccontextmanager
    async def metered(*_args, **kwargs):
        assert kwargs["workflow"] == "SOURCE_EVIDENCE_EXTRACT"
        yield SimpleNamespace(count=2)

    async def settle(observed_receipt, *, consumed_units):
        events.append(("settle", observed_receipt, consumed_units))

    monkeypatch.setattr(tasks, "llm_enabled", lambda: True)
    monkeypatch.setattr(tasks.cost_guard, "reserve", reserve)
    monkeypatch.setattr(tasks.cost_guard, "settle_reservation", settle)
    monkeypatch.setattr(tasks, "metered_llm_calls", metered)
    monkeypatch.setattr(tasks, "process_source_asset", lambda _source: [])

    result = asyncio.run(
        tasks._metered_process_source_asset(
            source,
            reservation_id="durable-attempt",
            operation_run_id=uuid.uuid4(),
        )
    )

    assert result == []
    assert events == [
        ("reserve", 3, "durable-attempt"),
        ("settle", receipt, 2),
    ]


def test_source_provider_boundary_stops_before_extraction_when_blocked(monkeypatch) -> None:
    async def reserve(**_kwargs):
        return CostGuardDecision(False, "kill switch")

    monkeypatch.setattr(tasks, "llm_enabled", lambda: True)
    monkeypatch.setattr(tasks.cost_guard, "reserve", reserve)
    monkeypatch.setattr(
        tasks,
        "process_source_asset",
        lambda _source: (_ for _ in ()).throw(AssertionError("provider boundary crossed")),
    )

    with pytest.raises(tasks._SourceProcessingCostBlocked, match="kill switch"):
        asyncio.run(
            tasks._metered_process_source_asset(
                _source(),
                reservation_id="blocked-attempt",
                operation_run_id=None,
            )
        )
