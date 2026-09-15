"""Transport helpers for provider calls whose answer must be a JSON object.

작가와 독립 검수자는 오랫동안 "JSON만 출력하세요"라고 요청한 뒤 응답 텍스트를
`json.loads`로 파싱했다. 모델이 한국어 인용문("증상이 없는데...")을 본문에 넣으면
문자열 값 안에 이스케이프되지 않은 큰따옴표가 그대로 들어가고, ```json fence까지
붙으면 완성된 글 한 편이 파싱 실패로 통째로 버려진다. 재시도는 같은 프롬프트로
생성을 다시 구매하므로 비용만 늘고 같은 실패를 반복한다.

도구 호출을 강제하면 공급자가 객체 자체를 보낸다. `tool_use.input`은 이미 파싱된
dict라서 fence도, 이스케이프도 개입할 여지가 없다. 이 모듈은 그 블록을 꺼내는
최소한의 어댑터이며, 프롬프트·검증기·계측은 호출부에 그대로 남는다.
"""

from __future__ import annotations

from typing import Any


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
    return None


def first_text(response: object) -> str:
    """레거시 텍스트 경로. 첫 번째 텍스트 블록을 그대로 돌려준다."""

    for block in getattr(response, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return ""
