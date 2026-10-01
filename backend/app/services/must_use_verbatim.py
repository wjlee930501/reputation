"""승인된 필수 문구(must_use_messages)의 원문 보존 판정.

작가 검증(`content_engine`)과 독립 검수(`content_ai_review`)가 **같은 규칙**을 쓴다.
작가는 필수 문구를 원문 그대로 독립된 문장으로 넣어야 저장되고, 검수자가 바로 그
문장을 인용해 지적하면 그 지적은 기록만 남고 발행을 막지 않는다. 두 판정이 다른
단위를 쓰면 작가 검증은 통과했는데 검수가 막는(또는 그 반대) 틈이 생긴다.

필수 문구 집합은 **현재 승인된 운영 기준(APPROVED)의 문구뿐이다**(`must_use_requirement`).
글에 저장된 콘텐츠 가이드(brief)의 문구나 운영자가 가이드에 덧붙인 문구는 작가 검증 대상도,
검수 면제 근거도 아니다 — 승인본이 바뀐 뒤(2cm→1cm) 옛 가이드가 철회된 문구를 되살리지
않게 한다. 의료광고 금지 표현 필터에 걸리는 문구는 요구하지도 면제하지도 않고, 그 사실을
`excluded`로 돌려 호출자가 경고·운영자 기록을 남기게 한다.

판정은 정규화한 문자열의 **완전 일치**뿐이다. 단어·어간 목록이나 유사도를 쓰지 않는다.
정규화 범위(이 밖의 차이는 모두 다른 문장이다):

- 유니코드 호환 문자 접기(NFKC와 같되 위·아래 첨자는 남긴다) — 전각/반각 문자·숫자·
  구두점(`，` `．` `２ｃｍ`), `㎝`, `…`를 같은 글자로 접는다. `cm²`의 `²`는 `2`가 아니다.
- 공백·zero-width 문자, 따옴표(`'` `"` `‘’` `“”` `「」` `『』` 등)와 마크다운 강조 기호
  (`*` `_` `` ` ``) 제거. 단, **숫자와 숫자 사이**에 있으면 지우지 않는다 — `1 0cm`이
  `10cm`이, `5*10`이 `510`이 되지 않는다(공백은 한 칸으로만 접는다).
- 문장 끝의 마침표(`.` `。`)만 제거한다. `?`와 `!`는 문장의 뜻이므로 남긴다.

쉼표·가운뎃점·괄호·소수점 같은 **문장 안의 구두점은 남긴다**(`1.0cm`과 `10cm`은 다르다).
조사·어미·단어는 한 글자라도 다르면 다른 문장이다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.models.essence import PhilosophyStatus
from app.utils.medical_filter import check_forbidden

_ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"
_QUOTES = "'\"`‘’‚‛“”„‟‹›«»「」『』〈〉《》"
# 비교에서 지우는 글자(공백·따옴표·강조·zero-width)의 연속. 숫자 사이의 것은 남긴다.
_REMOVABLE_RUN = re.compile(f"[\\s{re.escape(_QUOTES)}*_{_ZERO_WIDTH}]+")
# 마크다운 강조 기호. 문장을 나누기 전에 지워야 `**…다.** 다음 문장`이 한 문장으로 붙지 않는다.
_EMPHASIS_RUN = re.compile(r"[*_`]+")
_ZERO_WIDTH_CHARS = re.compile(f"[{_ZERO_WIDTH}]")
_WHITESPACE = re.compile(r"\s+")
_TERMINAL_PERIODS = ".。"
# 문장 경계: 종결 부호(뒤따르는 닫는 따옴표 포함) 뒤의 공백, 또는 줄바꿈.
# `2.5cm`처럼 공백이 없는 마침표는 경계가 아니다.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。])[\"'”’」』]*\s+|\n+")
# 줄 머리의 마크다운 구조 기호(제목·목록·인용). 문장 내용이 아니다.
_LINE_MARKER = re.compile(r"^\s*(?:#{1,6}\s+|[-+*]\s+|>\s*|\d+[.)]\s+)+")

# 짝이 맞는 따옴표 안의 인용(호환 문자 접기 뒤에도 남는 따옴표 쌍).
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

# 필수 문구를 요구·면제 대상에서 뺀 사유. 운영자 기록·로그의 기계 키다.
EXCLUDED_FORBIDDEN_EXPRESSION = "FORBIDDEN_EXPRESSION"
EXCLUDED_NOT_TEXT = "NOT_TEXT"


@lru_cache(maxsize=4096)
def _fold_character(char: str) -> str:
    """NFKC의 호환 분해를 한 글자에 적용하되 위·아래 첨자는 숫자로 접지 않는다."""

    decomposition = unicodedata.decomposition(char)
    if not decomposition.startswith("<") or decomposition.startswith(("<super>", "<sub>")):
        return char
    return "".join(
        _fold_character(chr(int(code, 16))) for code in decomposition.split()[1:]
    )


def _fold_compatibility(text: object) -> str:
    value = unicodedata.normalize("NFC", str(text or ""))
    return unicodedata.normalize("NFC", "".join(_fold_character(char) for char in value))


def _is_ascii_digit(char: str) -> bool:
    return "0" <= char <= "9"


def _between_digits(value: str, start: int, end: int) -> bool:
    return (
        start > 0
        and end < len(value)
        and _is_ascii_digit(value[start - 1])
        and _is_ascii_digit(value[end])
    )


def _strip_outside_numbers(pattern: re.Pattern[str], value: str) -> str:
    """`pattern`에 걸리는 글자를 지우되 숫자와 숫자 사이의 것은 남긴다."""

    def replace(match: re.Match[str]) -> str:
        if not _between_digits(value, match.start(), match.end()):
            return ""
        kept = _ZERO_WIDTH_CHARS.sub("", match.group())
        return _WHITESPACE.sub(" ", kept)

    return pattern.sub(replace, value)


def normalize_verbatim(text: object) -> str:
    """두 문구가 '원문 그대로'인지 비교하기 위한 정규화. 범위는 모듈 설명을 따른다."""

    value = _strip_outside_numbers(_REMOVABLE_RUN, _fold_compatibility(text))
    return value.rstrip(_TERMINAL_PERIODS)


def _sentences(text: object) -> list[str]:
    """정규화한 문장 목록. 빈 문장은 버린다."""

    value = _fold_compatibility(text)
    sentences: list[str] = []
    for line in value.splitlines():
        line = _strip_outside_numbers(_EMPHASIS_RUN, _LINE_MARKER.sub("", line))
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


@dataclass(frozen=True, slots=True)
class ExcludedMustUseMessage:
    """승인본에 있지만 원문 그대로 요구할 수 없어 뺀 필수 문구 하나.

    `index`는 승인본 `must_use_messages` 안의 0부터 센 위치다. 문구 원문은 싣지 않는다 —
    로그와 운영자 기록에는 위치와 걸린 금지 표현만 남기고, 원문은 승인본에서 본다.
    """

    index: int
    reason: str
    forbidden_expressions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MustUseRequirement:
    """현재 승인본에서 원문 그대로 요구하는 필수 문구와, 요구에서 뺀 문구."""

    messages: tuple[str, ...]
    excluded: tuple[ExcludedMustUseMessage, ...]


def approved_philosophy(philosophy: object | None) -> object | None:
    """현재 APPROVED 승인본이면 그대로, 아니면 None. 승인본이 아니면 요구·면제가 없다."""

    status = getattr(philosophy, "status", None)
    if philosophy is None or getattr(status, "value", status) != PhilosophyStatus.APPROVED.value:
        return None
    return philosophy


def must_use_requirement(philosophy: object | None) -> MustUseRequirement:
    """이 글이 원문 그대로 담아야 하는 필수 문구 — 현재 APPROVED 승인본 문구만.

    정규화 기준으로 중복을 없앤다. 의료광고 금지 표현 필터에 걸리는 문구는 원문 그대로
    쓰면 저장·발행 게이트가 반드시 막으므로 요구하지 않고, 조용히 버리지 않도록 `excluded`에
    남긴다 — 호출부가 로그와 운영자 기록을 남긴다(`must_use_exclusions`). 금지 표현
    필터가 우선한다.
    """

    approved = approved_philosophy(philosophy)
    if approved is None:
        return MustUseRequirement(messages=(), excluded=())
    seen: set[str] = set()
    required: list[str] = []
    excluded: list[ExcludedMustUseMessage] = []
    for index, candidate in enumerate(getattr(approved, "must_use_messages", None) or []):
        if not isinstance(candidate, str):
            excluded.append(ExcludedMustUseMessage(index=index, reason=EXCLUDED_NOT_TEXT))
            continue
        message = candidate.strip()
        key = normalize_verbatim(message)
        if not key or key in seen:
            continue
        seen.add(key)
        violations = check_forbidden(message)
        if violations:
            excluded.append(
                ExcludedMustUseMessage(
                    index=index,
                    reason=EXCLUDED_FORBIDDEN_EXPRESSION,
                    forbidden_expressions=tuple(violations),
                )
            )
            continue
        required.append(message)
    return MustUseRequirement(messages=tuple(required), excluded=tuple(excluded))


def required_must_use_messages(philosophy: object | None) -> list[str]:
    """원문 그대로 요구하고 검수 면제 근거가 되는 필수 문구(`must_use_requirement`의 messages)."""

    return list(must_use_requirement(philosophy).messages)


def missing_must_use_messages(body: object, messages: Iterable[str]) -> list[str]:
    """본문에 독립된 문장으로 원문 그대로 들어 있지 않은 필수 문구."""

    return [
        message for message in messages if not appears_as_standalone_sentence(body, message)
    ]


# 따옴표 없이 지적 문구 안에 통째로 들어 있으면 그 문장을 짚었다고 보는 최소 길이(정규화 뒤
# 글자 수). 이보다 짧은 문장("네.")은 아무 지적 문구에나 우연히 들어 있어 신호가 아니다.
_UNQUOTED_CITATION_MIN_CHARS = 8


def _cites_other_candidate_sentence(
    finding_message: object,
    approved: set[str],
    candidate_sentences: set[str],
    matched: str,
) -> bool:
    """지적 문구가 필수 문구가 아닌 후보의 다른 문장을 함께 짚었는가.

    구조적 신호 두 가지만 본다(단어 목록 없음).
    - 따옴표로 감싼 인용이 후보의 다른 문장 전체와 같다.
    - 따옴표 없이도 지적 문구(정규화)가 후보의 다른 문장 전체(정규화, 8자 이상)를 품는다.
      필수 문구 안에 통째로 들어 있는 짧은 문장은 필수 문구 인용의 일부라 신호가 아니다.
    짧은 표현만 짚은 지적은 후보의 한 문장과 같지 않으므로 여기서 걸리지 않는다.
    """

    others = {
        sentence
        for sentence in candidate_sentences
        if sentence not in approved and sentence not in matched
    }
    if not others:
        return False
    message = _fold_compatibility(finding_message)
    for groups in _QUOTED_SPAN.findall(message):
        if normalize_verbatim(next(group for group in groups if group)) in others:
            return True
    normalized_message = normalize_verbatim(message)
    return any(
        len(sentence) >= _UNQUOTED_CITATION_MIN_CHARS and sentence in normalized_message
        for sentence in others
    )


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
    3. 지적 문구가 필수 문구가 아닌 후보의 다른 문장을 함께 짚지 않는다 — 따옴표로 그 문장
       전체를 인용했거나, 따옴표 없이 그 문장(8자 이상) 전체를 지적 문구에 담았다.
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
    if _cites_other_candidate_sentence(
        finding_message, approved, candidate_sentences, normalize_verbatim(matched)
    ):
        return None
    return matched


__all__ = (
    "CANDIDATE_TEXT_FIELDS",
    "EXCLUDED_FORBIDDEN_EXPRESSION",
    "EXCLUDED_NOT_TEXT",
    "ExcludedMustUseMessage",
    "MustUseRequirement",
    "appears_as_standalone_sentence",
    "approved_philosophy",
    "matched_must_use_message",
    "must_use_requirement",
    "missing_must_use_messages",
    "normalize_verbatim",
    "required_must_use_messages",
)
