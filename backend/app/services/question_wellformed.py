"""측정 질문·글 제목의 결정적 문형 검사 — 비문이 저장·작가 프롬프트로 넘어가지 않게 한다.

2026-10-06 운영에서 "갑상선초음파 치료 비용이 얼마나 드는지 알려줘"(검사에 '치료'),
"마산 마산 심장초음파 진료 가능한 병원"(지역 중복), "마산 추천 진료를 받으려는데…"
(군더더기가 키워드 자리에 들어감)가 측정 질문으로 저장됐고, 같은 문형이 글 제목이 됐다.
사전·템플릿을 고치는 것만으로는 다음 미등록 시술명이 같은 사고를 낸다 — 그래서 템플릿
**결과 문장**을 마지막으로 한 번 더 검사한다. 모델 호출 없이 문자열 규칙만 쓴다.

의료광고 금지 표현 검사(`utils/medical_filter.py`)와는 목적이 다르다. 여기는 '말이 되는
문장인가'만 본다.
"""

from __future__ import annotations

import logging
import re

from app.services.keyword_analysis import (
    _SEARCH_FILLERS,
    KeywordClass,
    _match_key,
    analyze_keyword,
    normalize,
)

logger = logging.getLogger(__name__)

PROBLEM_REPEATED_TOKEN = "repeated_token"
PROBLEM_FILLER_KEYWORD = "filler_keyword"
PROBLEM_TREATMENT_ON_EXAM = "treatment_on_exam"

# 검사·촬영 이름. 시술(임플란트·수술)은 "치료"와 자연스럽게 붙으므로 넣지 않는다 —
# "치료 비용"이 말이 안 되는 것은 검사뿐이다.
_EXAM_TERM_RE = re.compile(
    r"(내시경|초음파|촬영|조영술|검사|검진|골밀도|심전도|ct|mri|엑스레이)$"
)
_TREATMENT_TOKEN_RE = re.compile(r"^치료")
# 군더더기 바로 뒤의 진료·치료 — "추천 진료를 받으려는데"처럼 키워드 자리가 비어 있다는 표지.
_CARE_WORD_RE = re.compile(r"^(진료|치료)(를|하는|하려면)?$")


def is_exam_term(term: object) -> bool:
    return bool(_EXAM_TERM_RE.search(_match_key(str(term or ""))))


def treatment_applied_to_exam(text: object) -> bool:
    """검사 이름 바로 뒤에 '치료…'가 붙은 문형인가("갑상선초음파 치료 비용")."""
    tokens = normalize(str(text or "")).split()
    return any(
        is_exam_term(prev) and _TREATMENT_TOKEN_RE.match(nxt)
        for prev, nxt in zip(tokens, tokens[1:])
    )


def question_problems(text: object, *, keyword: str | None = None) -> list[str]:
    """문장의 결함 코드 목록. 비어 있으면 정상이다.

    `keyword`는 템플릿에 들어간 키워드 조각이다 — 알면 키워드 자리가 군더더기·지역뿐인지
    직접 본다(`analyze_keyword`가 검색어 형태로 분류).
    """
    tokens = normalize(str(text or "")).split()
    problems: list[str] = []
    if any(
        len(_match_key(a)) >= 2 and _match_key(a) == _match_key(b)
        for a, b in zip(tokens, tokens[1:])
    ):
        problems.append(PROBLEM_REPEATED_TOKEN)

    filler_slot = any(
        _match_key(a) in _SEARCH_FILLERS and _CARE_WORD_RE.match(b)
        for a, b in zip(tokens, tokens[1:])
    )
    if keyword is not None and keyword.strip():
        filler_slot = (
            filler_slot
            or analyze_keyword(keyword).keyword_class is KeywordClass.SEARCH_PHRASE
        )
    if filler_slot:
        problems.append(PROBLEM_FILLER_KEYWORD)

    if treatment_applied_to_exam(text):
        problems.append(PROBLEM_TREATMENT_ON_EXAM)
    return problems


def question_is_wellformed(
    text: object, *, keyword: str | None = None, source: str = ""
) -> bool:
    """정상 문장이면 True. 결함이 있으면 경고 로그를 남기고 False."""
    problems = question_problems(text, keyword=keyword)
    if problems:
        logger.warning(
            "malformed question rejected source=%s problems=%s text=%r",
            source or "-",
            ",".join(problems),
            str(text or "")[:120],
        )
    return not problems
