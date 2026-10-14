"""한 작가 호출 안의 거절·재작성이 전송 몫을 다 쓴 것은 슬롯 예산 소진이 아니다(2026-10-14 감사).

작가 호출 하나(획득 하나)의 전송 상한은 `SEMANTIC_TRANSPORT_LIMIT`(3)이다. `generate_content`의
재작성 회차와 전송 재시도(강제 tool_choice 거절 뒤 auto 재시도·tenacity)가 그 몫을 다 쓰면
`ACQUISITION_TRANSPORT_EXHAUSTED`가 올라온다. 종전에는 그 예외가 그대로 `GENERATION_BUDGET_EXHAUSTED`
로 분류돼 주제 교체 계단을 곧바로 탔다 — 실제로는 결정적 거절(표본 실패)이었다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.content import ContentType
from app.services import content_engine
from app.workers.generation_attempt_state import (
    ACQUISITION_TRANSPORT_EXHAUSTED,
    SEMANTIC_TRANSPORT_LIMIT,
    GenerationBudgetExceeded,
    fresh_generation_attempt,
    reserve_acquisition,
    transport_attempt_started,
)
from app.workers.generation_retry_policy import GENERATION_BUDGET_EXHAUSTED_CODE
from app.workers.generation_run_control import classify_generation_failure


def _ledger_observer() -> tuple[dict, callable]:
    """실제 획득 원장과 같은 규칙으로 전송 시작을 세는 관찰자."""

    state = {
        "attempt": reserve_acquisition(
            fresh_generation_attempt(topic_id="topic-a"), acquisition_id="writer-1", kind="WRITER"
        )
    }

    def observe() -> None:
        state["attempt"] = transport_attempt_started(state["attempt"], acquisition_id="writer-1")

    return state, observe


@pytest.fixture
def no_must_use(monkeypatch):
    async def none(*_args, **_kwargs):
        return []

    monkeypatch.setattr(content_engine, "approved_must_use_messages_with_record", none)


async def test_a_rewrite_that_runs_out_of_transport_ends_as_the_rejection(monkeypatch, no_must_use):
    """회차마다 두 번 전송(강제 tool_choice 거절 + auto)하는 작가: 2회차 도중 전송 몫이 끝난다."""

    _state, observe = _ledger_observer()
    rounds: list[int] = []

    async def attempt(*_args, **_kwargs):
        context = _args[-1]
        rounds.append(len(rounds) + 1)
        for _ in range(2):
            observe()
            context["http_attempt"] = int(context.get("http_attempt") or 0) + 1
        raise ValueError("SEO hard-fail: title is too short")

    monkeypatch.setattr(content_engine, "_generate_content_attempt", attempt)

    with pytest.raises(ValueError, match="SEO hard-fail") as raised:
        await content_engine.generate_content(
            SimpleNamespace(id="h1", name="전송의원"),
            ContentType.FAQ,
            _attempt_context={"logical_call_id": "writer-1", "http_attempt": 0},
        )

    assert rounds == [1, 2]
    assert not isinstance(raised.value, GenerationBudgetExceeded)
    # 표본 실패로 분류된다 — 예산 소진(주제 교체 계단)이 아니다.
    assert classify_generation_failure(raised.value)[0] == "GENERATION_REJECTED"


async def test_transport_exhaustion_without_a_rejection_is_an_environment_failure(
    monkeypatch, no_must_use
):
    """거절 없이 전송 재시도만으로 몫이 끝났으면 공급자 쪽 불안정이다(하루 예산이 다시 열린다)."""

    _state, observe = _ledger_observer()

    async def attempt(*_args, **_kwargs):
        for _ in range(SEMANTIC_TRANSPORT_LIMIT + 1):
            observe()
        raise AssertionError("unreachable")

    monkeypatch.setattr(content_engine, "_generate_content_attempt", attempt)

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await content_engine.generate_content(
            SimpleNamespace(id="h1", name="전송의원"),
            ContentType.FAQ,
            _attempt_context={"logical_call_id": "writer-1", "http_attempt": 0},
        )

    assert str(raised.value) == ACQUISITION_TRANSPORT_EXHAUSTED
    assert classify_generation_failure(raised.value)[0] == "GENERATION_FAILED"


@pytest.mark.parametrize(
    "marker",
    ["TEXT_HTTP_ATTEMPTS_EXHAUSTED", "WRITER_RESULTS_EXHAUSTED", "TOPIC_SWAP_EXHAUSTED"],
)
def test_slot_and_topic_ceilings_stay_budget_exhaustion(marker):
    assert classify_generation_failure(GenerationBudgetExceeded(marker))[0] == (
        GENERATION_BUDGET_EXHAUSTED_CODE
    )
