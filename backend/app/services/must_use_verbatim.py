"""승인된 필수 문구(must_use_messages)의 원문 보존 판정.

작가 검증(`content_engine`)과 독립 검수(`content_ai_review`)가 **같은 규칙**을 쓴다.
작가는 필수 문구를 원문 그대로 독립된 문장으로 넣어야 저장되고, 검수자가 바로 그
문장을 인용해 지적하면 그 지적은 기록만 남고 발행을 막지 않는다. 두 판정이 다른
단위를 쓰면 작가 검증은 통과했는데 검수가 막는(또는 그 반대) 틈이 생긴다.

판정은 정규화한 문자열의 **완전 일치**뿐이다. 단어·어간 목록이나 유사도를 쓰지 않는다.
정규화 범위(이 밖의 차이는 모두 다른 문장이다):

- 유니코드 NFKC — 전각/반각 문자·숫자·구두점(`，` `．` `２ｃｍ`)과 `…`를 같은 글자로 접는다.
- 모든 공백과 zero-width 문자 제거 — 띄어쓰기·줄바꿈 차이는 같은 문장이다.
- 따옴표(`'` `"` `‘’` `“”` `「」` `『』` 등)와 마크다운 강조 기호(`*` `_` `` ` ``) 제거.
- 문장 끝의 종결 부호(`.` `!` `?` `。`)만 제거한다.

쉼표·가운뎃점·괄호 같은 **문장 안의 구두점은 남긴다**. 조사·어미·단어는 한 글자라도
다르면 다른 문장이다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from app.utils.medical_filter import check_forbidden

_REMOVED_CHARACTERS = {
    ord(char): None
    for char in (
        "'\"`‘’‚‛“”„‟‹›«»「」『』〈〉《》"
        "*_"
        "\u200b\u200c\u200d\u2060\ufeff"
    )
}
_TERMINAL_PUNCTUATION = ".!?。"
_WHITESPACE = re.compile(r"\s+")
# 문장 경계: 종결 부호(뒤따르는 닫는 따옴표 포함) 뒤의 공백, 또는 줄바꿈.
# `2.5cm`처럼 공백이 없는 마침표는 경계가 아니다.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。])[\"'”’」』]*\s+|\n+")
# 마크다운 강조 기호. 문장을 나누기 전에 지워야 `**…다.** 다음 문장`이 한 문장으로 붙지 않는다.
_EMPHASIS = str.maketrans("", "", "*_`")
# 줄 머리의 마크다운 구조 기호(제목·목록·인용). 문장 내용이 아니다.
_LINE_MARKER = re.compile(r"^\s*(?:#{1,6}\s+|[-+*]\s+|>\s*|\d+[.)]\s+)+")

# 짝이 맞는 따옴표 안의 인용(NFKC 뒤에도 남는 따옴표 쌍).
_QUOTED_SPAN = re.compile(
    r"'([^']+)'|\"([^\"]+)\"|‘([^’]+)’|“([^”]+)”|「([^」]+)」|『([^』]+)』"
)

# 검수 인용을 찾는 후보의 공개 텍스트 필드. 작가 검증은 본문만 본다.
CANDIDATE_TEXT_FIELDS = (
    "title",
    "body",
    "meta_description",
    "faq_question",
    "faq_answer_summary",
)


def normalize_verbatim(text: object) -> str:
    """두 문구가 '원문 그대로'인지 비교하기 위한 정규화. 범위는 모듈 설명을 따른다."""

    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.translate(_REMOVED_CHARACTERS)
    value = _WHITESPACE.sub("", value)
    return value.rstrip(_TERMINAL_PUNCTUATION)


def _sentences(text: object) -> list[str]:
    """정규화한 문장 목록. 빈 문장은 버린다."""

    value = unicodedata.normalize("NFKC", str(text or ""))
    sentences: list[str] = []
    for line in value.splitlines():
        line = _LINE_MARKER.sub("", line).translate(_EMPHASIS)
        for part in _SENTENCE_BOUNDARY.split(line):
            normalized = normalize_verbatim(part)
            if normalized:
                sentences.append(normalized)
    return sentences


def _contains_run(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(
        haystack[index : index + width] == needle
        for index in range(len(haystack) - width + 1)
    )


def appears_as_standalone_sentence(text: object, message: object) -> bool:
    """`message`가 `text` 안에 앞뒤로 덧붙인 말 없이 독립된 문장(들)로 들어 있는가.

    필수 문구가 여러 문장이면 그 문장들이 같은 순서로 연달아 있어야 한다. 필수 문구를
    다른 문장 속에 끼워 넣거나 말을 덧붙이면 일치가 아니다.
    """

    return _contains_run(_sentences(text), _sentences(message))


def required_must_use_messages(
    philosophy: object | None, content_brief: dict[str, Any] | None
) -> list[str]:
    """이 글이 원문 그대로 담아야 하는 승인된 필수 문구.

    승인된 운영 기준과 콘텐츠 가이드의 `must_use_messages`를 합치고 정규화 기준으로
    중복을 없앤다. 의료광고 금지 표현 필터에 걸리는 문구는 원문 그대로 쓰면 저장·발행
    게이트가 반드시 막으므로 요구하지 않는다 — 금지 표현 필터가 우선한다.
    """

    candidates: Iterable[object] = [
        *(getattr(philosophy, "must_use_messages", None) or []),
        *((content_brief or {}).get("must_use_messages") or []),
    ]
    seen: set[str] = set()
    required: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        message = candidate.strip()
        key = normalize_verbatim(message)
        if not key or key in seen or check_forbidden(message):
            continue
        seen.add(key)
        required.append(message)
    return required


def missing_must_use_messages(body: object, messages: Iterable[str]) -> list[str]:
    """본문에 독립된 문장으로 원문 그대로 들어 있지 않은 필수 문구."""

    return [
        message for message in messages if not appears_as_standalone_sentence(body, message)
    ]


def _cites_other_candidate_sentence(
    finding_message: object, approved: set[str], candidate_sentences: set[str]
) -> bool:
    """지적 문구가 필수 문구가 아닌 후보의 다른 문장 전체를 따옴표로 함께 인용했는가.

    짧은 표현 인용은 후보의 한 문장과 같지 않으므로 여기서 걸리지 않는다.
    """

    for groups in _QUOTED_SPAN.findall(unicodedata.normalize("NFKC", str(finding_message or ""))):
        normalized = normalize_verbatim(next(group for group in groups if group))
        if normalized in candidate_sentences and normalized not in approved:
            return True
    return False


def matched_must_use_message(
    *,
    quote: object,
    finding_message: object,
    messages: Iterable[str],
    candidate: dict[str, Any],
) -> str | None:
    """검수 지적이 인용한 문장이 후보 안의 필수 문구 원문 그대로이면 그 문구를 돌려준다.

    세 조건을 모두 만족해야 일치다.
    1. 인용문(`quote`)을 정규화한 값이 필수 문구 하나를 정규화한 값과 완전히 같다.
       필수 문구의 일부만 인용했거나 말을 덧붙여 인용했으면 일치가 아니다.
    2. 그 필수 문구가 후보의 공개 필드 안에 독립된 문장(들)로 들어 있다. 본문이 필수
       문구에 말을 덧붙여 한 문장으로 썼으면 여기서 떨어진다.
    3. 지적 문구가 필수 문구가 아닌 후보의 다른 문장 전체를 따옴표로 함께 인용하지 않는다.
       한 지적이 두 문장을 같이 문제 삼으면 필수 문구 일치만으로 내릴 수 없다.
    """

    normalized_quote = normalize_verbatim(quote)
    if not normalized_quote:
        return None
    messages = list(messages)
    matched = next(
        (message for message in messages if normalize_verbatim(message) == normalized_quote),
        None,
    )
    if matched is None:
        return None
    if not any(
        appears_as_standalone_sentence(candidate.get(field), matched)
        for field in CANDIDATE_TEXT_FIELDS
    ):
        return None
    candidate_sentences = {
        sentence
        for field in CANDIDATE_TEXT_FIELDS
        for sentence in _sentences(candidate.get(field))
    }
    approved = {normalize_verbatim(message) for message in messages}
    if _cites_other_candidate_sentence(finding_message, approved, candidate_sentences):
        return None
    return matched


__all__ = (
    "CANDIDATE_TEXT_FIELDS",
    "appears_as_standalone_sentence",
    "matched_must_use_message",
    "missing_must_use_messages",
    "normalize_verbatim",
    "required_must_use_messages",
)
