"""OpenRouter 오류는 상태 코드로 분류한다 — 과부하·서버 오류는 환경 장애(감사 F9).

`content_engine`의 tenacity는 `reraise=True`로 3회 뒤 원래 openai 예외를 다시 던진다. 종전에는
429·5xx(`APIStatusError` 하위)와 연결 오류(`APIConnectionError`)가 모두 `GENERATION_FAILED`로
떨어져, 기다리면 풀리는 공급자 장애를 원인 불명 실패로 보고했다.
"""

from __future__ import annotations

import httpx
import openai
import pytest

from app.workers.generation_run_control import classify_generation_failure

_REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


def _status(cls, status: int):
    return cls("x", response=httpx.Response(status, request=_REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (_status(openai.InternalServerError, 502), "PROVIDER_UNAVAILABLE"),
        (_status(openai.RateLimitError, 429), "PROVIDER_UNAVAILABLE"),
        (_status(openai.APIStatusError, 503), "PROVIDER_UNAVAILABLE"),
        (openai.APIConnectionError(request=_REQUEST), "PROVIDER_UNAVAILABLE"),
        (openai.APITimeoutError(request=_REQUEST), "PROVIDER_TIMEOUT"),
        (_status(openai.BadRequestError, 400), "GENERATION_FAILED"),
        (_status(openai.APIStatusError, 402), "GENERATION_FAILED"),
    ],
    ids=["502", "429", "503", "connection", "timeout", "400", "402-credits"],
)
def test_openrouter_errors_are_classified_by_status(error, code):
    assert classify_generation_failure(error)[0] == code


def test_openrouter_monthly_key_limit_has_a_specific_terminal_cause():
    error = openai.PermissionDeniedError(
        "Key limit exceeded (monthly limit)",
        response=httpx.Response(403, request=_REQUEST),
        body={"error": {"message": "Key limit exceeded (monthly limit)"}},
    )

    code, message = classify_generation_failure(error)

    assert code == "OPENROUTER_KEY_LIMIT_EXCEEDED"
    assert "OpenRouter API 키의 월 사용 한도" in message


def test_other_openrouter_403_is_not_misclassified_as_a_key_limit():
    error = openai.PermissionDeniedError(
        "User not allowed to access this model",
        response=httpx.Response(403, request=_REQUEST),
        body={"error": {"message": "User not allowed to access this model"}},
    )

    assert classify_generation_failure(error)[0] == "GENERATION_FAILED"
