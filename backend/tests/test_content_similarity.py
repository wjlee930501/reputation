"""한국어 주제 유사도 — 중복 제목 가드와 참고자료 적합성 채점의 보정 기준."""

from app.services.content_similarity import (
    REFERENCE_TOKEN_MATCH_MIN,
    char_bigrams,
    find_similar_titles,
    normalize_topic_text,
    reference_topic_match,
    topic_similarity,
)
from app.utils.authority_sources import institution_title_tokens


def test_normalize_strips_spaces_and_punctuation():
    assert normalize_topic_text("허리 디스크(추간판탈출증)!") == "허리디스크추간판탈출증"
    assert normalize_topic_text(None) == ""


def test_char_bigrams_of_short_text():
    assert char_bigrams("요통") == {"요통"}
    assert char_bigrams("무") == {"무"}
    assert char_bigrams("") == set()


def test_topic_similarity_is_symmetric_and_bounded():
    assert topic_similarity("허리디스크 치료", "허리디스크 치료") == 1.0
    assert topic_similarity("허리디스크", "대장암") == 0.0
    score = topic_similarity("무릎 통증 원인", "무릎 통증의 원인")
    assert 0.0 < score < 1.0
    assert score == topic_similarity("무릎 통증의 원인", "무릎 통증 원인")


def test_near_duplicate_titles_are_flagged():
    matches = find_similar_titles(
        "허리디스크 초기 증상과 자가 진단법",
        "허리디스크 초기 증상",
        "허리디스크",
        [
            "허리디스크 초기증상과 자가진단 방법",
            "무릎 관절염 초기 증상과 관리법",
        ],
    )

    assert [title for title, _score in matches] == ["허리디스크 초기증상과 자가진단 방법"]
    assert matches[0][1] >= 0.6


def test_identical_normalized_title_scores_one():
    matches = find_similar_titles(
        "허리 디스크, 수술 없이 치료할 수 있나요?",
        "비수술 치료의 범위",
        "허리디스크",
        ["허리디스크 수술 없이 치료할 수 있나요"],
    )

    assert matches == [("허리디스크 수술 없이 치료할 수 있나요", 1.0)]


def test_same_keyword_different_angle_is_not_flagged():
    matches = find_similar_titles(
        "허리디스크 수술 없이 회복할 수 있을까요?",
        "비수술 치료를 고려하는 기준",
        "허리디스크",
        [
            "허리디스크 초기 증상과 자가 진단법",
            "허리디스크 환자가 피해야 할 자세",
            "허리디스크 재발을 막는 생활 습관",
        ],
    )

    assert matches == []


def test_first_h2_catches_a_repeated_angle_under_the_same_keyword():
    matches = find_similar_titles(
        "허리디스크, 이런 증상이면 병원에 오세요",
        "허리디스크 초기증상과 자가진단 방법",
        "허리디스크",
        ["허리디스크 초기 증상과 자가 진단법"],
    )

    assert [title for title, _score in matches] == ["허리디스크 초기 증상과 자가 진단법"]


def test_duplicate_scan_ignores_blank_and_repeated_titles():
    matches = find_similar_titles(
        "무릎 통증 원인과 치료",
        "",
        "무릎 통증",
        ["", None, "무릎 통증 원인과 치료", "무릎 통증 원인과 치료"],
    )

    assert matches == [("무릎 통증 원인과 치료", 1.0)]


# ── 참고자료 주제 적합성 ────────────────────────────────────────────────

_DISC_ARTICLE_TERMS = [
    "허리디스크",
    "허리디스크 초기 증상과 치료 방법",
    "허리디스크는 왜 생기나요",
    "비수술 치료",
]

_KNEE_ARTICLE_TERMS = [
    "무릎 통증",
    "무릎 통증 원인과 치료 방법",
    "무릎이 아픈 이유",
    "무릎 관절 비수술 치료",
]


def _match(reference_title: str, terms: list[str]) -> float | None:
    return reference_topic_match(
        reference_title, terms, ignored_tokens=institution_title_tokens()
    )


def test_related_kdca_page_stays_attached_to_the_article():
    score = _match("질병관리청 국가건강정보포털 - 요통", _DISC_ARTICLE_TERMS)

    assert score is not None
    assert score >= REFERENCE_TOKEN_MATCH_MIN


def test_unrelated_page_scores_below_the_threshold():
    score = _match("질병관리청 국가건강정보포털 - 대장암", _KNEE_ARTICLE_TERMS)

    assert score is not None
    assert score < REFERENCE_TOKEN_MATCH_MIN


def test_compound_disease_name_counts_as_the_same_topic():
    """'추간판탈출증'은 '허리디스크' 글의 같은 주제다 — bigram 비율만으로 보면 낮다."""
    score = _match("서울아산병원 질환백과 - 추간판탈출증", _DISC_ARTICLE_TERMS)

    assert score == 1.0


def test_other_department_reference_is_unrelated():
    score = _match("대한피부과학회 아토피피부염 진료지침", _DISC_ARTICLE_TERMS)

    assert score is not None
    assert score < REFERENCE_TOKEN_MATCH_MIN


def test_english_only_title_cannot_be_judged():
    assert _match("Mayo Clinic - Herniated disk", _DISC_ARTICLE_TERMS) is None


def test_institution_and_generic_words_alone_cannot_be_judged():
    assert _match("대한정형외과학회 진료 지침", _DISC_ARTICLE_TERMS) is None


def test_empty_article_terms_cannot_be_judged():
    assert _match("질병관리청 국가건강정보포털 - 대장암", []) is None
