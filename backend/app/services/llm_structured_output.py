"""Transport helpers for provider calls whose answer must be a JSON object.

작가와 독립 검수자는 오랫동안 "JSON만 출력하세요"라고 요청한 뒤 응답 텍스트를
`json.loads`로 파싱했다. 모델이 한국어 인용문("증상이 없는데...")을 본문에 넣으면
문자열 값 안에 이스케이프되지 않은 큰따옴표가 그대로 들어가고, ```json fence까지
붙으면 완성된 글 한 편이 파싱 실패로 통째로 버려진다. 재시도는 같은 프롬프트로
생성을 다시 구매하므로 비용만 늘고 같은 실패를 반복한다.

도구 호출을 강제하면 공급자가 객체 자체를 보낸다. 도구 호출의 파싱된 인자는 이미
dict라서 fence도, 이스케이프도 개입할 여지가 없다. 이 모듈은 그 블록을 꺼내는
최소한의 어댑터이며, 프롬프트·검증기·계측은 호출부에 그대로 남는다.

프로덕션 응답은 OpenRouter(OpenAI chat completions 형태)다 — `choices[].message.
tool_calls[].function.arguments`(JSON 문자열)와 `choices[].message.content`를 읽는다.
기존 테스트 더블의 Anthropic 형태(`content[]`의 `tool_use`/`text` 블록)도 같은
의미로 받아들인다 — 어댑터가 두 표기를 모두 읽어야 호출부 계약이 하나로 유지된다.
"""

from __future__ import annotations

import json
from typing import Any


def _openai_tool_call_input(response: object, tool_name: str) -> dict[str, Any] | None:
    for choice in getattr(response, "choices", None) or []:
        message = getattr(choice, "message", None)
        for call in getattr(message, "tool_calls", None) or []:
            function = getattr(call, "function", None)
            if getattr(function, "name", None) != tool_name:
                continue
            arguments = getattr(function, "arguments", None)
            if isinstance(arguments, str):
                try:
                    parsed = json.loads(arguments)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
            if isinstance(arguments, dict):
                return dict(arguments)
    return None


def tool_use_input(response: object, *, tool_name: str) -> dict[str, Any] | None:
    """강제된 도구 호출의 파싱된 입력. 텍스트로만 답한 응답이면 None."""

    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != tool_name:
            continue
        value = getattr(block, "input", None)
        if isinstance(value, dict):
            return dict(value)
    return _openai_tool_call_input(response, tool_name)


def first_text(response: object) -> str:
    """레거시 텍스트 경로. 첫 번째 텍스트 블록/choice 텍스트를 그대로 돌려준다."""

    for block in getattr(response, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    for choice in getattr(response, "choices", None) or []:
        content = getattr(getattr(choice, "message", None), "content", None)
        if isinstance(content, str):
            return content
    return ""


def incomplete_reason(response: object) -> str | None:
    """잘림/거절로 끝난 이유. 정상 종결이면 None.

    Anthropic 형태는 `stop_reason`(max_tokens/refusal), OpenAI 형태는
    `choices[].finish_reason`(length/refusal/content_filter)을 읽는다.
    """

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason in {"max_tokens", "refusal"}:
        return str(stop_reason)
    for choice in getattr(response, "choices", None) or []:
        reason = getattr(choice, "finish_reason", None)
        if reason in {"length", "refusal", "content_filter"}:
            return str(reason)
    return None
