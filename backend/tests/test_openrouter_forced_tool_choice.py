"""강제 tool_choice를 거절하는 모델에서도 작가·독립 검수가 도구 호출로 동작한다.

운영 회귀(2026-10-01): `CLAUDE_MODEL=anthropic/claude-sonnet-5.5` 전환 뒤 OpenRouter의
모든 공급자(Claude Platform on AWS·Azure·Anthropic·Google Vertex·Amazon Bedrock)가
`tool_choice: type "tool" and "any" are not supported for this model.` 400으로 거절해
04:00·07:00 원고 생성이 전부 실패했다. 이 400일 때만 같은 요청을 `tool_choice="auto"`로
1회 다시 보내고, 강제를 받는 모델은 종전 payload를 그대로 쓴다.
"""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.models.content import ContentType
from app.services import content_ai_review, content_engine, openrouter

FORCED_SUPPORTED_MODEL = "anthropic/claude-sonnet-5"
FORCED_REJECTING_MODEL = "anthropic/claude-sonnet-5.5"

# 운영 로그(worker-00200-ffx, item 47b36df6)의 본문 모양 그대로.
_PROVIDER_RAW = (
    '{"message":"tool_choice: type \\"tool\\" and \\"any\\" are not supported '
    'for this model."}'
)


def _bad_request(body: dict) -> openai.BadRequestError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return openai.BadRequestError(
        f"Error code: 400 - {{'error': {body!r}}}",
        response=httpx.Response(400, request=request),
        body=body,
    )


def _forced_rejection() -> openai.BadRequestError:
    return _bad_request(
        {
            "message": "Provider returned error",
            "code": 400,
            "metadata": {
                "raw": _PROVIDER_RAW,
                "provider_name": "Amazon Bedrock",
                "is_byok": False,
                "previous_errors": [
                    {
                        "code": 400,
                        "message": "Provider returned error",
                        "provider_name": name,
                        "raw": _PROVIDER_RAW,
                    }
                    for name in (
                        "Claude Platform on AWS",
                        "Azure",
                        "Anthropic",
                        "Google",
                    )
                ],
            },
        }
    )


def _other_bad_request() -> openai.BadRequestError:
    return _bad_request(
        {
            "message": "Provider returned error",
            "code": 400,
            "metadata": {
                "raw": '{"message":"max_tokens: 99999 > 64000, which is the maximum"}',
                "provider_name": "Anthropic",
            },
        }
    )


def _tool_response(name: str, payload: dict, *, response_id: str = "gen-tool") -> SimpleNamespace:
    call = SimpleNamespace(
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(payload, ensure_ascii=False)),
    )
    return SimpleNamespace(
        id=response_id,
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(content=None, tool_calls=[call]),
            )
        ],
    )


def _text_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="gen-text",
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=text, tool_calls=None),
            )
        ],
    )


class _ScriptedCompletions:
    def __init__(self, handlers: list) -> None:
        self.handlers = list(handlers)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        handler = self.handlers[len(self.calls) - 1]
        if isinstance(handler, BaseException):
            raise handler
        return handler


def _client(handlers: list) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=_ScriptedCompletions(handlers)))


@pytest.fixture(autouse=True)
def _fresh_forced_tool_choice_memory():
    openrouter.reset_forced_tool_choice_memory_for_tests()
    yield
    openrouter.reset_forced_tool_choice_memory_for_tests()


@pytest.fixture
def ledger(monkeypatch) -> SimpleNamespace:
    """provider_usage 시도 원장과 cost_guard 실제 호출 계수를 기록한다."""
    attempts: list[dict] = []
    provider_calls: list[str] = []
    settled: list[int] = []

    async def record_attempt(**kwargs):
        attempts.append(kwargs)
        return True

    async def record_provider_call(category, **_kwargs):
        provider_calls.append(category)

    async def settle(_receipt, *, consumed_units, **_kwargs):
        settled.append(consumed_units)

    monkeypatch.setattr("app.services.provider_usage.record_attempt", record_attempt)
    monkeypatch.setattr("app.services.cost_guard.record_provider_call", record_provider_call)
    monkeypatch.setattr("app.services.cost_guard.settle_reservation", settle)
    return SimpleNamespace(attempts=attempts, provider_calls=provider_calls, settled=settled)


# ── 오류 식별 ─────────────────────────────────────────────────────


def test_forced_rejection_is_identified_from_the_production_error_shape():
    assert openrouter.is_forced_tool_choice_unsupported(_forced_rejection()) is True


@pytest.mark.parametrize(
    "exc",
    [
        _other_bad_request(),
        _bad_request({"message": "tool_choice must name one of the provided tools"}),
        RuntimeError(f"Error code: 400 - {_PROVIDER_RAW}"),
        openai.APIConnectionError(
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        ),
    ],
)
def test_other_errors_are_not_treated_as_forced_rejection(exc):
    assert openrouter.is_forced_tool_choice_unsupported(exc) is False


# ── 헬퍼 계약 ─────────────────────────────────────────────────────


def _request(**overrides) -> dict:
    request = {
        "model": FORCED_REJECTING_MODEL,
        "max_tokens": 100,
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": "규칙"}]},
            {"role": "user", "content": "원고를 쓰세요."},
        ],
        "tools": [
            openrouter.function_tool(name="emit", description="d", input_schema={"type": "object"})
        ],
    }
    request.update(overrides)
    return request


async def test_helper_merges_nothing_into_caller_extra_body_on_both_attempts():
    extra_body = {"usage": {"include": True}, "provider": {"sort": "price"}}
    request = _request(extra_body=extra_body)
    original_messages = json.loads(json.dumps(request["messages"]))
    client = _client([_forced_rejection(), _tool_response("emit", {"ok": True})])

    await openrouter.create_required_tool_completion(client, tool_name="emit", **request)

    calls = client.chat.completions.calls
    assert len(calls) == 2
    for call in calls:
        assert call["extra_body"] == {"usage": {"include": True}, "provider": {"sort": "price"}}
    assert extra_body == {"usage": {"include": True}, "provider": {"sort": "price"}}
    assert request["messages"] == original_messages


def test_directive_is_appended_to_list_content_and_to_missing_user_turn():
    directive = openrouter.required_tool_call_directive("emit")

    listed = openrouter.require_tool_call_messages(
        [{"role": "user", "content": [{"type": "text", "text": "본문"}]}], tool_name="emit"
    )
    assert listed[-1]["content"][-1] == {"type": "text", "text": directive}

    system_only = openrouter.require_tool_call_messages(
        [{"role": "system", "content": "규칙"}], tool_name="emit"
    )
    assert system_only == [
        {"role": "system", "content": "규칙"},
        {"role": "user", "content": directive},
    ]


async def test_rejected_model_is_remembered_per_model_only():
    client = _client(
        [
            _forced_rejection(),
            _tool_response("emit", {}),
            _tool_response("emit", {}),
            _tool_response("emit", {}),
        ]
    )

    await openrouter.create_required_tool_completion(client, tool_name="emit", **_request())
    await openrouter.create_required_tool_completion(client, tool_name="emit", **_request())
    await openrouter.create_required_tool_completion(
        client, tool_name="emit", **_request(model=FORCED_SUPPORTED_MODEL)
    )

    choices = [call["tool_choice"] for call in client.chat.completions.calls]
    assert choices == [
        openrouter.forced_tool_choice("emit"),
        "auto",
        "auto",
        openrouter.forced_tool_choice("emit"),
    ]
    assert openrouter.forced_tool_choice_supported(FORCED_REJECTING_MODEL) is False
    assert openrouter.forced_tool_choice_supported(FORCED_SUPPORTED_MODEL) is True


# ── 작가(content_engine) ──────────────────────────────────────────


def _hospital() -> SimpleNamespace:
    return SimpleNamespace(
        id=None,
        name="테스트병원",
        address="서울 강남구",
        phone="02-000-0000",
        business_hours={},
        region=["강남"],
        specialties=["외과"],
        keywords=["복통"],
        director_name="김원장",
        director_career="외과 전문의",
        director_philosophy="충분히 설명합니다.",
        treatments=[],
    )


def _article() -> dict:
    return {
        "title": "복통 진료 전 확인할 점",
        "body": "## 검진 안내\n테스트병원 김원장은 강남에서 충분히 설명합니다. "
        + ("본문 문장입니다. " * 280),
        "meta_description": "검진 전에 확인할 점을 정리했습니다.",
        "references": [],
        "faq_question": None,
        "faq_answer_summary": None,
    }


def _install_writer(monkeypatch, handlers: list, *, model: str) -> _ScriptedCompletions:
    completions = _ScriptedCompletions(handlers)
    monkeypatch.setattr(content_engine.client.chat.completions, "create", completions.create)
    monkeypatch.setattr(content_engine.settings, "CLAUDE_MODEL", model)
    return completions


async def _write(context: dict) -> dict:
    return await content_engine._generate_content_attempt(
        _hospital(), ContentType.NOTICE, _attempt_context=context
    )


async def test_writer_forced_supported_model_keeps_existing_payload(monkeypatch, ledger):
    completions = _install_writer(
        monkeypatch,
        [_tool_response(content_engine.ARTICLE_TOOL_NAME, _article())],
        model=FORCED_SUPPORTED_MODEL,
    )
    context = {"logical_call_id": "writer-1", "http_attempt": 0}

    result = await _write(context)

    assert result["title"] == _article()["title"]
    assert len(completions.calls) == 1
    call = completions.calls[0]
    assert list(call) == ["model", "max_tokens", "messages", "tools", "tool_choice"]
    assert call["model"] == FORCED_SUPPORTED_MODEL
    assert call["tool_choice"] == {
        "type": "function",
        "function": {"name": content_engine.ARTICLE_TOOL_NAME},
    }
    assert "도구를 호출해" not in call["messages"][-1]["content"]
    assert [a["http_attempt"] for a in ledger.attempts] == [1]
    assert ledger.provider_calls == ["content"]
    assert context["http_attempt"] == 1


async def test_writer_retries_once_with_auto_after_forced_rejection(monkeypatch, ledger):
    completions = _install_writer(
        monkeypatch,
        [
            _forced_rejection(),
            _tool_response(content_engine.ARTICLE_TOOL_NAME, _article(), response_id="gen-auto"),
        ],
        model=FORCED_REJECTING_MODEL,
    )
    context = {"logical_call_id": "writer-2", "http_attempt": 0}

    result = await _write(context)

    assert result["title"] == _article()["title"]
    forced, auto = completions.calls
    assert forced["tool_choice"] == openrouter.forced_tool_choice(content_engine.ARTICLE_TOOL_NAME)
    assert auto["tool_choice"] == "auto"
    assert auto["tools"] == forced["tools"]
    assert auto["model"] == forced["model"] == FORCED_REJECTING_MODEL
    assert auto["max_tokens"] == forced["max_tokens"]
    assert auto["messages"][0] == forced["messages"][0]
    original_user = forced["messages"][-1]["content"]
    assert auto["messages"][-1]["content"] == (
        f"{original_user}\n\n"
        + openrouter.required_tool_call_directive(content_engine.ARTICLE_TOOL_NAME)
    )
    assert "extra_body" not in auto

    # 거절된 강제 시도와 auto 재시도가 각자 한 줄씩 원장·계수에 남는다.
    assert [
        (a["attempt_id"], a["http_attempt"], a.get("usage_known"), a.get("provider_request_id"))
        for a in ledger.attempts
    ] == [
        ("writer-2:http:1", 1, False, None),
        ("writer-2:http:2", 2, None, "gen-auto"),
    ]
    assert ledger.provider_calls == ["content", "content"]
    assert context["http_attempt"] == 2

    # 같은 프로세스의 다음 원고는 400을 다시 맞지 않고 바로 auto로 나간다.
    completions.handlers.append(
        _tool_response(content_engine.ARTICLE_TOOL_NAME, _article(), response_id="gen-next")
    )
    await _write(context)
    assert len(completions.calls) == 3
    assert completions.calls[2]["tool_choice"] == "auto"
    assert ledger.provider_calls == ["content"] * 3
    assert context["http_attempt"] == 3


async def test_writer_other_bad_request_is_not_retried(monkeypatch, ledger):
    completions = _install_writer(
        monkeypatch, [_other_bad_request()], model=FORCED_REJECTING_MODEL
    )
    context = {"logical_call_id": "writer-3", "http_attempt": 0}

    with pytest.raises(openai.BadRequestError):
        await _write(context)

    assert len(completions.calls) == 1
    assert openrouter.forced_tool_choice_supported(FORCED_REJECTING_MODEL) is True
    assert [(a["http_attempt"], a["usage_known"]) for a in ledger.attempts] == [(1, False)]
    assert ledger.provider_calls == ["content"]


async def test_writer_auto_reply_without_tool_call_uses_existing_failure(monkeypatch, ledger):
    completions = _install_writer(
        monkeypatch,
        [_forced_rejection(), _text_response("알겠습니다. 원고를 작성하겠습니다.")],
        model=FORCED_REJECTING_MODEL,
    )
    context = {"logical_call_id": "writer-4", "http_attempt": 0}

    with pytest.raises(ValueError, match="invalid JSON"):
        await _write(context)

    assert [call["tool_choice"] for call in completions.calls] == [
        openrouter.forced_tool_choice(content_engine.ARTICLE_TOOL_NAME),
        "auto",
    ]
    assert [a["http_attempt"] for a in ledger.attempts] == [1, 2]


async def test_writer_auto_retry_transport_failure_records_both_attempts(monkeypatch, ledger):
    _install_writer(
        monkeypatch,
        [_forced_rejection(), openai.APIConnectionError(request=httpx.Request("POST", "http://x"))],
        model=FORCED_REJECTING_MODEL,
    )
    context = {"logical_call_id": "writer-5", "http_attempt": 0}
    monkeypatch.setattr(
        content_engine._generate_content_attempt.retry, "stop", lambda _state: True
    )

    with pytest.raises(openai.APIConnectionError):
        await _write(context)

    assert [(a["attempt_id"], a["usage_known"]) for a in ledger.attempts] == [
        ("writer-5:http:1", False),
        ("writer-5:http:2", False),
    ]
    assert ledger.provider_calls == ["content", "content"]


# ── 독립 검수(content_ai_review) ──────────────────────────────────

_PASS_VERDICT = {"decision": "PASS", "confidence": 0.97, "findings": [], "summary": "이상 없음"}
_LOW_CONFIDENCE_VERDICT = {
    "decision": "PASS",
    "confidence": 0.55,
    "findings": [],
    "summary": "확신이 부족합니다.",
}


async def _provider_review(client, *, model: str, counter: dict | None = None):
    return await content_ai_review._provider_review(
        client=client,
        payload="{}",
        model=model,
        hospital=_hospital(),
        content={"title": "제목", "body": "본문"},
        decision=SimpleNamespace(receipt=None),
        logical_call_id="review-1",
        attempt_id="review-1:http:1",
        http_attempt=1,
        attempt_counter=counter,
    )


async def test_review_forced_supported_model_keeps_existing_payload(ledger):
    client = _client([_tool_response(content_ai_review.REVIEW_TOOL_NAME, _PASS_VERDICT)])

    review = await _provider_review(client, model=FORCED_SUPPORTED_MODEL)

    assert review.status == content_ai_review.ContentAiReviewStatus.PASS
    (call,) = client.chat.completions.calls
    assert list(call) == ["model", "max_tokens", "messages", "tools", "tool_choice"]
    assert call["tool_choice"] == {
        "type": "function",
        "function": {"name": content_ai_review.REVIEW_TOOL_NAME},
    }
    assert call["messages"][-1] == {"role": "user", "content": "{}"}
    assert [a["attempt_id"] for a in ledger.attempts] == ["review-1:http:1"]
    assert ledger.provider_calls == ["content"]
    assert ledger.settled == [1]


async def test_review_retries_once_with_auto_after_forced_rejection(ledger):
    client = _client(
        [
            _forced_rejection(),
            _tool_response(content_ai_review.REVIEW_TOOL_NAME, _PASS_VERDICT, response_id="rv"),
        ]
    )
    counter: dict = {}

    review = await _provider_review(client, model=FORCED_REJECTING_MODEL, counter=counter)

    assert review.status == content_ai_review.ContentAiReviewStatus.PASS
    forced, auto = client.chat.completions.calls
    assert forced["tool_choice"] == openrouter.forced_tool_choice(
        content_ai_review.REVIEW_TOOL_NAME
    )
    assert auto["tool_choice"] == "auto"
    assert auto["tools"] == forced["tools"]
    assert auto["messages"][0] == forced["messages"][0]
    assert auto["messages"][-1]["content"] == "{}\n\n" + openrouter.required_tool_call_directive(
        content_ai_review.REVIEW_TOOL_NAME
    )
    assert [
        (a["attempt_id"], a["http_attempt"], a.get("usage_known"), a.get("provider_request_id"))
        for a in ledger.attempts
    ] == [
        ("review-1:http:1", 1, False, None),
        ("review-1:http:1:tool-choice-auto", 2, None, "rv"),
    ]
    assert ledger.provider_calls == ["content", "content"]
    # 예약은 논리 검수 1건 단위다 — 재시도로 두 번 정산하지 않는다.
    assert ledger.settled == [1]
    assert counter == {"http_attempt": 2}


async def test_review_other_bad_request_is_not_retried(ledger):
    client = _client([_other_bad_request()])

    review = await _provider_review(client, model=FORCED_REJECTING_MODEL)

    assert review.status == content_ai_review.ContentAiReviewStatus.UNAVAILABLE
    assert review.unavailable_reason == (
        content_ai_review.ContentAiReviewUnavailableReason.PROVIDER_ERROR
    )
    assert len(client.chat.completions.calls) == 1
    assert openrouter.forced_tool_choice_supported(FORCED_REJECTING_MODEL) is True
    assert [a["usage_known"] for a in ledger.attempts] == [False]


async def test_review_auto_reply_without_tool_call_uses_existing_failure(ledger):
    client = _client([_forced_rejection(), _text_response("검수를 진행했습니다.")])

    review = await _provider_review(client, model=FORCED_REJECTING_MODEL)

    assert review.status == content_ai_review.ContentAiReviewStatus.UNAVAILABLE
    assert review.unavailable_reason == (
        content_ai_review.ContentAiReviewUnavailableReason.INVALID_RESPONSE
    )
    assert [call["tool_choice"] for call in client.chat.completions.calls] == [
        openrouter.forced_tool_choice(content_ai_review.REVIEW_TOOL_NAME),
        "auto",
    ]


async def test_review_escalation_numbers_after_the_auto_retry(monkeypatch, ledger):
    client = _client(
        [
            _forced_rejection(),
            _tool_response(content_ai_review.REVIEW_TOOL_NAME, _LOW_CONFIDENCE_VERDICT),
            _tool_response(content_ai_review.REVIEW_TOOL_NAME, _PASS_VERDICT),
        ]
    )

    async def reserve(category, **_kwargs):
        return SimpleNamespace(allowed=True, receipt=SimpleNamespace(category=category))

    monkeypatch.setattr(content_ai_review, "_llm_client", lambda: client)
    monkeypatch.setattr(content_ai_review.cost_guard, "reserve", reserve)
    monkeypatch.setattr(content_ai_review.settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(content_ai_review.settings, "CLAUDE_MODEL_FAST", FORCED_REJECTING_MODEL)
    monkeypatch.setattr(content_ai_review.settings, "CLAUDE_MODEL", FORCED_SUPPORTED_MODEL)

    review = await content_ai_review.review_generated_content(
        hospital=SimpleNamespace(id=None, name="병원"),
        philosophy=SimpleNamespace(),
        content={"title": "안내", "body": "환자마다 다릅니다."},
        content_brief=None,
        logical_call_id="review-2",
    )

    assert review.status == content_ai_review.ContentAiReviewStatus.PASS
    assert [call["tool_choice"] for call in client.chat.completions.calls] == [
        openrouter.forced_tool_choice(content_ai_review.REVIEW_TOOL_NAME),
        "auto",
        openrouter.forced_tool_choice(content_ai_review.REVIEW_TOOL_NAME),
    ]
    assert [(a["attempt_id"], a["http_attempt"]) for a in ledger.attempts] == [
        ("review-2:http:1", 1),
        ("review-2:http:1:tool-choice-auto", 2),
        ("review-2:escalated:http:3", 3),
    ]
    assert ledger.provider_calls == ["content"] * 3
