"""키워드에 섞인 지역어·검색 군더더기가 질문에 되풀이되지 않는 계약.

배경(마산 병원 월간 보고서): 키워드 '마산 심장초음파'·'마산 내과 추천'이 지역 목록
['경남 창원시', '마산합포구']와 정확히 일치하지 않아 지역이 안 떼어졌고,
"마산합포구 마산 심장초음파 진료 가능한 병원" · "… 마산 추천 진료 가능한 병원"이 나갔다.
"""

from __future__ import annotations

import pytest

from app.services.keyword_analysis import (
    KeywordClass,
    analyze_keyword,
)
from app.services.sov_engine import generate_query_matrix_specs

REGION = ["경남 창원시", "마산합포구"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("마산 심장초음파", "심장초음파"),
        ("창원 심장초음파", "심장초음파"),
        ("마산합포구 심장초음파", "심장초음파"),
        ("경남 창원시 심장초음파", "심장초음파"),
    ],
)
def test_region_stem_in_keyword_is_stripped(raw, expected):
    analysis = analyze_keyword(raw, REGION)
    assert analysis.canonical_term == expected
    assert analysis.embedded_region is not None


@pytest.mark.parametrize("raw", ["추천", "마산 추천", "마산 내과 추천", "잘하는곳", "근처 병원"])
def test_filler_only_keyword_is_search_phrase(raw):
    assert analyze_keyword(raw, REGION).keyword_class is KeywordClass.SEARCH_PHRASE


def test_filler_is_dropped_next_to_clinical_term():
    assert analyze_keyword("마산 심장초음파 추천", REGION).canonical_term == "심장초음파"


def test_rendered_questions_do_not_repeat_region_or_use_filler_keyword():
    specs = generate_query_matrix_specs(
        REGION, ["내과"], ["마산 내과 추천", "마산 심장초음파"]
    )
    texts = [text for text, _ in specs]
    assert not any("마산 추천" in t or "마산 마산" in t for t in texts)
    assert not any("마산합포구 마산" in t for t in texts)
    # 심장초음파는 검사라 "진료 가능한 병원"이 아니라 "가능한 병원 추천해줘" 문형을 받는다.
    assert "마산합포구 심장초음파 가능한 병원 추천해줘" in texts
    # 군더더기뿐인 키워드는 {keyword} 템플릿에서 빠진다.
    assert not any("추천 진료" in t or "추천 치료" in t for t in texts)


def test_clinical_keyword_unaffected_by_region_prefix_rule():
    # 임상어가 지역어의 접두가 아니면 그대로다.
    assert analyze_keyword("대장내시경", ["서울", "대장동"]).canonical_term == "대장내시경"
