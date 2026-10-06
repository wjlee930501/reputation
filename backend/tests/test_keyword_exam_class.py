"""검사 키워드는 '치료' 문형을 받지 않는다 — 가상 키워드만 쓴다.

2026-10 마케팅 검토: '갑상선초음파'가 분류 사전에 없어 '모름'으로 접히고, 질환과 같은
"{keyword} 치료 비용이 얼마나 드는지 알려줘" 질문이 만들어졌다. 그 질문을 본 작가가 글 제목을
"갑상선초음파 치료 비용"으로 지었다. 초음파는 검사다.
"""

import pytest

from app.services.keyword_analysis import KeywordClass, analyze_keyword
from app.services.sov_engine import generate_query_matrix_specs


@pytest.mark.parametrize(
    "keyword,canonical",
    [
        ("갑상선초음파", "갑상선초음파"),
        ("경동맥초음파", "경동맥초음파"),
        ("심장초음파", "심장초음파"),
        ("흉부엑스레이촬영", "흉부엑스레이촬영"),
        ("관상동맥조영술", "관상동맥조영술"),
        ("골밀도", "골밀도 검사"),
        ("심전도", "심전도 검사"),
    ],
)
def test_exam_keywords_are_procedures(keyword, canonical):
    analysis = analyze_keyword(keyword)
    assert analysis.keyword_class is KeywordClass.PROCEDURE
    assert analysis.canonical_term == canonical


def test_exam_keyword_questions_never_say_treatment():
    questions = [
        text
        for text, _ in generate_query_matrix_specs(
            ["가상시", "가상동"], ["내과"], ["갑상선초음파", "골밀도"]
        )
    ]
    exam_questions = [q for q in questions if "초음파" in q or "골밀도" in q]
    assert exam_questions
    assert not any("치료" in q for q in exam_questions)
    assert "가상시에서 갑상선초음파 받을 수 있는 병원 알려줘" in questions
    assert "가상동 골밀도 검사 가능한 병원 추천해줘" in questions


def test_disease_keyword_still_gets_treatment_phrasing():
    questions = [
        text for text, _ in generate_query_matrix_specs(["가상시", "가상동"], ["내과"], ["당뇨"])
    ]
    assert "가상시에서 당뇨 치료하는 병원 알려줘" in questions
