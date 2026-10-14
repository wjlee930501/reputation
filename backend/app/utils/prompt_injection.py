"""자료 원문의 프롬프트 주입 의심 문구 판정 — 자동 요약 입력 제외와 병원 정보(자동) 줄 거르기 공용."""

from __future__ import annotations

import re

PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"이전\s*(?:모든\s*)?(?:지시|명령)(?:를|을)?\s*(?:무시|잊)"),
    re.compile(r"(?:system|developer)\s*prompt", re.IGNORECASE),
    re.compile(r"자동\s*승인(?:하라|해|하세요)"),
    re.compile(r"<\/?(?:system|developer|assistant)>", re.IGNORECASE),
)


def has_prompt_injection(*parts: str | None) -> bool:
    text = "\n".join(part for part in parts if part)
    return any(pattern.search(text) for pattern in PROMPT_INJECTION_PATTERNS)


__all__ = ("PROMPT_INJECTION_PATTERNS", "has_prompt_injection")
