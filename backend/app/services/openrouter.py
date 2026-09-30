"""OpenRouter — 모든 LLM·이미지 호출의 단일 게이트웨이.

콘텐츠 생성·Essence·SoV 측정·이미지 생성/검수가 전부 `OPENROUTER_API_KEY` 하나로
나간다. 모델 식별자는 전부 `vendor/model` 슬러그(`anthropic/claude-sonnet-5`,
`openai/gpt-5.6-luna`, `google/gemini-3.6-flash` …)다.

OpenRouter는 OpenAI 호환 API다 — `openai` SDK가 chat completions·Responses·usage
형식을 그대로 쓴다. Anthropic Messages·google-genai SDK는 더 이상 쓰지 않는다.

재시도 정책 계약: SDK 내부 재시도는 HTTP 시도를 숨기므로 모든 클라이언트를
`max_retries=0`으로 만들고, 복원력은 호출부의 tenacity가 소유한다. 각 실제 HTTP
시도는 provider_usage 원장에 따로 기록한다.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from openai import AsyncOpenAI, OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# 서버 사이드 검색 도구. 모델이 필요하다고 판단할 때만 호출한다(tool_choice=auto와
# 같은 의미). OpenAI·Google·Anthropic 모델은 공급자 네이티브 검색으로 실행된다.
WEB_SEARCH_TOOL = {"type": "openrouter:web_search"}

_clients_lock = threading.Lock()
_sync_clients: dict[float, OpenAI] = {}
_async_clients: dict[float, AsyncOpenAI] = {}


def configured() -> bool:
    return bool(settings.OPENROUTER_API_KEY.strip())


def sync_client(*, timeout: float = 90.0) -> OpenAI:
    with _clients_lock:
        client = _sync_clients.get(timeout)
        if client is None:
            client = OpenAI(
                api_key=settings.OPENROUTER_API_KEY,
                base_url=OPENROUTER_BASE_URL,
                timeout=timeout,
                max_retries=0,
            )
            _sync_clients[timeout] = client
        return client


def async_client(*, timeout: float = 120.0) -> AsyncOpenAI:
    with _clients_lock:
        client = _async_clients.get(timeout)
        if client is None:
            client = AsyncOpenAI(
                api_key=settings.OPENROUTER_API_KEY,
                base_url=OPENROUTER_BASE_URL,
                timeout=timeout,
                max_retries=0,
            )
            _async_clients[timeout] = client
        return client


def reset_clients_for_tests() -> None:
    """테스트가 settings/생성자를 바꿔치기한 뒤 캐시를 비우기 위한 훅."""
    with _clients_lock:
        _sync_clients.clear()
        _async_clients.clear()


# ── Anthropic Messages → OpenAI chat completions 번역 ────────────────
#
# 기존 호출부는 Anthropic 모양(강제 tool_use, cache_control 시스템 블록,
# output_config json_schema)을 쓴다. OpenRouter는 같은 의미를 OpenAI 형식으로
# 받는다 — 여기서 한 번만 번역해 호출부 계약을 유지한다.


def function_tool(*, name: str, description: str, input_schema: dict[str, Any]) -> dict:
    """Anthropic tool 정의(`input_schema`)를 OpenAI function tool로 변환한다."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": input_schema,
        },
    }


def forced_tool_choice(name: str) -> dict:
    """Anthropic `tool_choice={type:"tool",name}`의 OpenAI 대응값."""
    return {"type": "function", "function": {"name": name}}


def system_content(system: str | list[dict[str, Any]]) -> Any:
    """system 문자열 또는 Anthropic system 블록 목록을 OpenAI 메시지 content로 변환.

    블록의 `cache_control`은 그대로 둔다 — OpenRouter가 Anthropic 계열 모델에
    프롬프트 캐시 breakpoint로 전달한다.
    """
    if isinstance(system, str):
        return system
    parts = []
    for block in system:
        part = {"type": "text", "text": str(block.get("text") or "")}
        cache_control = block.get("cache_control")
        if isinstance(cache_control, dict):
            part["cache_control"] = cache_control
        parts.append(part)
    return parts


def system_message(system: str | list[dict[str, Any]]) -> dict:
    return {"role": "system", "content": system_content(system)}


def json_schema_format(schema: dict[str, Any], *, name: str) -> dict:
    """Anthropic `output_config.format`의 OpenAI `response_format` 대응값."""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }


def tool_call_input(response: Any, *, tool_name: str) -> dict[str, Any] | None:
    """강제된 function call의 파싱된 인자. 텍스트로만 답한 응답이면 None."""
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


def first_text(response: Any) -> str:
    """첫 번째 choice의 텍스트 본문. 없으면 빈 문자열."""
    for choice in getattr(response, "choices", None) or []:
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content
    return ""


def finish_is_incomplete(response: Any) -> str | None:
    """잘림/거절 finish_reason을 돌려준다(Anthropic stop_reason 대응).

    OpenAI 계열은 `length`(max_tokens)·`refusal`·`content_filter`가 완료가 아닌
    종료다. `stop`/`tool_calls`는 정상 종결이다.
    """
    for choice in getattr(response, "choices", None) or []:
        reason = getattr(choice, "finish_reason", None)
        if reason in {"length", "refusal", "content_filter"}:
            return str(reason)
    return None


def response_model(response: Any) -> str | None:
    """실제로 답한 모델 슬러그(OpenRouter는 요청과 다른 라우팅 모델을 돌려줄 수 있다)."""
    model = getattr(response, "model", None)
    return str(model) if model else None


def extract_annotations_urls(response: Any) -> list[str]:
    """chat completions 응답의 url_citation 주석에서 URL 목록을 뽑는다."""
    urls: list[Any] = []
    for choice in getattr(response, "choices", None) or []:
        message = getattr(choice, "message", None)
        for annotation in getattr(message, "annotations", None) or []:
            citation = getattr(annotation, "url_citation", None)
            if citation is not None:
                urls.append(getattr(citation, "url", None))
            else:
                urls.append(getattr(annotation, "url", None))
    return urls


def web_search_requests(usage: Any) -> int | None:
    """`usage.server_tool_use.web_search_requests` — 모델이 실제로 발행한 검색 수."""
    server_tool_use = getattr(usage, "server_tool_use", None)
    if server_tool_use is None and isinstance(usage, dict):
        server_tool_use = usage.get("server_tool_use")
    if server_tool_use is None:
        return None
    value = (
        server_tool_use.get("web_search_requests")
        if isinstance(server_tool_use, dict)
        else getattr(server_tool_use, "web_search_requests", None)
    )
    return int(value) if isinstance(value, (int, float)) else None


def usage_cost_usd(usage: Any) -> float | None:
    """`usage: {include: true}`를 요청했을 때 OpenRouter가 돌려주는 실비(USD)."""
    value = getattr(usage, "cost", None)
    if value is None and isinstance(usage, dict):
        value = usage.get("cost")
    return float(value) if isinstance(value, (int, float)) else None


# ── 재시도 분류 ──────────────────────────────────────────────────────

import openai  # noqa: E402

# 같은 요청을 다시내도 결과가 바뀌지 않는 클라이언트 오류.
NON_RETRYABLE_LLM_ERRORS: tuple[type[BaseException], ...] = (
    openai.BadRequestError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
)


def is_retryable_llm_error(exc: BaseException) -> bool:
    """결정적 4xx면 False — 즉시 중단하고 호출부의 폴백으로 넘긴다."""
    return not isinstance(exc, NON_RETRYABLE_LLM_ERRORS)


# ── 강제 도구 호출을 거절하는 모델 ───────────────────────────────────
#
# 일부 모델은 강제 tool_choice(OpenAI `{type:function}`·`required` = Anthropic
# type tool/any)를 모든 공급자에서 400으로 거절한다. OpenRouter endpoints 목록은 그런
# 모델도 `tool_choice`를 지원한다고 광고하므로 공급자 라우팅(require_parameters·order·
# ignore)으로는 거를 수 없다. 모델 목록을 하드코딩하지 않고, 그 400을 실제로 받은
# 모델만 프로세스 안에서 기억해 tools는 그대로 둔 채 `tool_choice="auto"`로 보낸다.

_FORCED_TOOL_CHOICE_UNSUPPORTED_SIGNATURE = (
    "tool_choice: type tool and any are not supported for this model"
)
_forced_tool_choice_lock = threading.Lock()
_forced_tool_choice_unsupported_models: set[str] = set()


def _normalized_error_text(exc: BaseException) -> str:
    """오류 메시지와 본문을 이스케이프·따옴표 없이 소문자 한 줄로 편다.

    공급자 원문은 `metadata.raw` 안에 JSON 문자열로 이중 이스케이프돼 온다.
    """
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(json.dumps(body, ensure_ascii=False, default=str))
    text = re.sub(r"[\\\"']", "", " ".join(parts))
    return " ".join(text.lower().split())


def is_forced_tool_choice_unsupported(exc: BaseException) -> bool:
    """모델이 강제 tool_choice 자체를 거절한 400인지. 다른 400은 False다."""
    if not isinstance(exc, openai.BadRequestError):
        return False
    return _FORCED_TOOL_CHOICE_UNSUPPORTED_SIGNATURE in _normalized_error_text(exc)


def forced_tool_choice_supported(model: str) -> bool:
    with _forced_tool_choice_lock:
        return model not in _forced_tool_choice_unsupported_models


def remember_forced_tool_choice_unsupported(model: str) -> None:
    with _forced_tool_choice_lock:
        _forced_tool_choice_unsupported_models.add(model)


def reset_forced_tool_choice_memory_for_tests() -> None:
    with _forced_tool_choice_lock:
        _forced_tool_choice_unsupported_models.clear()


def required_tool_call_directive(tool_name: str) -> str:
    return (
        f"반드시 `{tool_name}` 도구를 호출해 결과를 제출하세요. "
        "도구 호출 없이 텍스트로만 답하지 마세요."
    )


def require_tool_call_messages(
    messages: list[dict[str, Any]], *, tool_name: str
) -> list[dict[str, Any]]:
    """마지막 user 메시지 끝에 도구 호출 지시문을 붙인 사본.

    system 블록은 건드리지 않는다 — 프롬프트 캐시 접두어가 그대로 남아야 한다.
    """
    directive = required_tool_call_directive(tool_name)
    copied = [dict(message) for message in messages]
    for message in reversed(copied):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = f"{content}\n\n{directive}" if content else directive
            return copied
        if isinstance(content, list):
            message["content"] = [*content, {"type": "text", "text": directive}]
            return copied
        break
    copied.append({"role": "user", "content": directive})
    return copied


async def create_required_tool_completion(
    client: Any,
    *,
    tool_name: str,
    on_forced_tool_choice_rejected: Callable[[BaseException], Awaitable[None]] | None = None,
    **request: Any,
) -> Any:
    """`tool_name` 호출을 요구하는 chat completion 한 건(HTTP 최대 2회).

    강제를 받는 모델에는 종전 그대로 `forced_tool_choice`로 보낸다. 강제 미지원 400이면
    그 모델을 기억하고, 같은 요청을 `tool_choice="auto"`와 도구 호출 지시문으로 정확히
    1회 다시 보낸다. 거절된 시도는 재시도 직전에 `on_forced_tool_choice_rejected`로
    넘겨 호출부가 시도 원장과 실제 호출 계수에 따로 남기게 한다. 이미 기억된 모델은
    처음부터 auto로 보낸다. auto 응답에 도구 호출이 없으면 호출부의 기존 파서가
    처리한다. `extra_body` 등 나머지 인자는 두 시도 모두 그대로 전달한다.
    """
    model = str(request.get("model") or "")
    loop = asyncio.get_running_loop()

    def send(payload: dict[str, Any]) -> Awaitable[Any]:
        return loop.run_in_executor(None, lambda: client.chat.completions.create(**payload))

    if forced_tool_choice_supported(model):
        try:
            return await send({**request, "tool_choice": forced_tool_choice(tool_name)})
        except openai.BadRequestError as exc:
            if not is_forced_tool_choice_unsupported(exc):
                raise
            remember_forced_tool_choice_unsupported(model)
            logger.warning(
                "forced tool_choice rejected by model=%s; retrying tool=%s with auto",
                model,
                tool_name,
            )
            if on_forced_tool_choice_rejected is not None:
                await on_forced_tool_choice_rejected(exc)
    return await send(
        {
            **request,
            "tool_choice": "auto",
            "messages": require_tool_call_messages(request["messages"], tool_name=tool_name),
        }
    )


# ── 이미지 생성 — 전용 /images 엔드포인트 ────────────────────────────
#
# OpenRouter의 이미지 API는 OpenAI SDK의 /images/generations 경로가 아니라
# `/api/v1/images`다. 모델별 supported_parameters(aspect_ratio·quality·resolution)가
# 다르므로 호출부가 허용 파라미터만 넘긴다.


class ImageResponseError(RuntimeError):
    """2xx로 왔지만 이미지 응답으로 읽을 수 없는 본문.

    종전에는 `response.json()`을 그대로 불러 빈 200·잘린 본문·HTML 오류 페이지가
    `json.JSONDecodeError`로 터졌다. 운영에 남는 문자열은 "Expecting value: line 1
    column 1 (char 0)"뿐이라 게이트웨이가 무엇을 돌려줬는지 사후에 좁힐 수 없고,
    호출부의 실패 분류기(`_image_failure_class`)가 훑을 quota 신호도 사라진다.
    상태·content-type·본문 조각을 메시지에 실어 그 구분을 복원한다.

    HTTP 상태를 속성으로 붙이지 않는다 — 붙이면 이미지 호출부의 transient 분류가
    2xx를 "재시도 불가 4xx가 아닌 것"이 아니라 결정적 응답으로 읽어 버린다.
    """


# 게이트웨이 본문 조각의 상한. 4xx 경로(400자)보다 짧게 잡는다 — 여기 실리는 것은
# 구조화된 공급자 오류가 아니라 정체를 알 수 없는 바이트라 더 실어도 얻는 게 없다.
_IMAGE_BODY_SNIPPET_LIMIT = 200


def _decode_image_response(response: httpx.Response) -> dict[str, Any]:
    """2xx 이미지 응답을 dict로 읽거나, 무엇이 왔는지 말하는 오류로 바꾼다."""
    try:
        body = response.json()
    except ValueError as exc:
        snippet = " ".join(response.text[:_IMAGE_BODY_SNIPPET_LIMIT].split())
        raise ImageResponseError(
            f"openrouter images {response.status_code} returned an unparsable body "
            f"(content-type={response.headers.get('content-type') or 'unknown'}, "
            f"bytes={len(response.content)}): {exc}"
            + (f" :: {snippet}" if snippet else " :: (empty body)")
        ) from exc
    if not isinstance(body, dict):
        raise ImageResponseError(
            f"openrouter images {response.status_code} returned a "
            f"{type(body).__name__} body where a JSON object was expected"
        )
    return body


def generate_image(
    *,
    model: str,
    prompt: str,
    aspect_ratio: str = "16:9",
    quality: str | None = None,
    resolution: str | None = None,
    timeout: float = 180.0,
) -> tuple[list[bytes], dict[str, Any]]:
    """POST /images → (디코딩된 PNG 목록, 원본 응답 dict).

    실패는 httpx/SDK 예외로 올린다 — 호출부의 재시도 분류가 판단한다.
    """
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "n": 1,
    }
    if quality:
        payload["quality"] = quality
    if resolution:
        payload["resolution"] = resolution
    response = httpx.post(
        f"{OPENROUTER_BASE_URL}/images",
        headers={
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    if response.status_code >= 400:
        # 본문의 공급자 사유(IMAGE_SAFETY·PROHIBITED_CONTENT·moderation_blocked 등)가
        # 호출부의 정책-차단/결정적-4xx 분류기가 보는 유일한 신호다. 잘라서 싣는다.
        detail = response.text[:400].replace("\n", " ")
        error = httpx.HTTPStatusError(
            f"openrouter images {response.status_code}: {detail}",
            request=response.request,
            response=response,
        )
        raise error
    body = _decode_image_response(response)
    images: list[bytes] = []
    for item in body.get("data") or []:
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json")
        if b64:
            images.append(base64.b64decode(b64, validate=True))
    return images, body


__all__ = (
    "OPENROUTER_BASE_URL",
    "WEB_SEARCH_TOOL",
    "configured",
    "sync_client",
    "async_client",
    "reset_clients_for_tests",
    "function_tool",
    "forced_tool_choice",
    "system_message",
    "json_schema_format",
    "tool_call_input",
    "first_text",
    "finish_is_incomplete",
    "response_model",
    "extract_annotations_urls",
    "web_search_requests",
    "usage_cost_usd",
    "NON_RETRYABLE_LLM_ERRORS",
    "is_retryable_llm_error",
    "is_forced_tool_choice_unsupported",
    "forced_tool_choice_supported",
    "remember_forced_tool_choice_unsupported",
    "reset_forced_tool_choice_memory_for_tests",
    "required_tool_call_directive",
    "require_tool_call_messages",
    "create_required_tool_completion",
    "ImageResponseError",
    "generate_image",
)
