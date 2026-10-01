"""필수 문구 원문 판정의 정규화가 숫자 값을 바꾸지 않는다 + 따옴표 없는 다른 문장 지적.

정규화는 띄어쓰기·따옴표·강조 기호 차이를 같은 문장으로 접지만, 숫자와 숫자 사이의 공백·
기호까지 지우면 값이 바뀐다(`1 0cm`→`10cm`, `5*10`→`510`, `10³`→`103`). 그런 문장은 필수
문구 원문이 아니며, 검수 면제 근거가 되어서도 안 된다. 공급자·네트워크를 쓰지 않는다.
"""

from __future__ import annotations

import pytest

from app.services.must_use_verbatim import (
    appears_as_standalone_sentence,
    matched_must_use_message,
    normalize_verbatim,
)
from tests.provider_network_guard_support import (
    forbid_external_network as _forbid_external_network_fixture,  # noqa: F401
)

APPROVED = "선종은 1cm 이상이면 제거를 권합니다."


@pytest.fixture(autouse=True)
def _no_external_calls(forbid_external_network):
    return forbid_external_network


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param("1 0cm", "10cm", id="space-between-digits"),
        pytest.param("1​ 0cm", "10cm", id="zero-width-and-space-between-digits"),
        pytest.param("1.0cm", "10cm", id="decimal-point"),
        pytest.param("5*10", "510", id="asterisk-between-digits"),
        pytest.param("5_10", "510", id="underscore-between-digits"),
        pytest.param("5'10\"", "510", id="quote-between-digits"),
        pytest.param("10³ 개", "103 개", id="superscript"),
        pytest.param("1,000명", "1000명", id="thousands-comma"),
        # 숫자 사이에 남기는 기호는 둘레의 공백도 그대로 둔다(보수적으로 다른 문장).
        pytest.param("5 * 10", "5*10", id="spaces-around-kept-symbol"),
    ],
)
def test_digits_joined_by_removed_characters_keep_their_value(left: str, right: str) -> None:
    assert normalize_verbatim(left) != normalize_verbatim(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param("2 cm", "2cm", id="space-before-unit"),
        pytest.param("２ｃｍ", "2cm", id="fullwidth"),
        pytest.param("1  0", "1 0", id="space-run-between-digits"),
        pytest.param("**1cm** 이상", "1cm 이상", id="emphasis-not-between-digits"),
    ],
)
def test_formatting_only_differences_stay_equal(left: str, right: str) -> None:
    assert normalize_verbatim(left) == normalize_verbatim(right)


def test_question_and_exclamation_marks_are_part_of_the_sentence() -> None:
    assert normalize_verbatim("검사를 받아야 합니까?") != normalize_verbatim("검사를 받아야 합니까.")
    assert normalize_verbatim("검사를 받아야 합니다!") != normalize_verbatim("검사를 받아야 합니다")
    assert normalize_verbatim("검사를 받아야 합니다.") == normalize_verbatim("검사를 받아야 합니다")


def test_a_body_that_splits_a_number_does_not_contain_the_message() -> None:
    assert appears_as_standalone_sentence(f"안내입니다. {APPROVED}", APPROVED)
    for altered in ("선종은 1 0cm 이상이면 제거를 권합니다.", "선종은 1**0cm 이상이면 제거를 권합니다."):
        assert not appears_as_standalone_sentence(f"안내입니다. {altered}", "선종은 10cm 이상이면 제거를 권합니다.")
    # 강조 기호가 숫자 사이가 아니면 종전처럼 원문 그대로다.
    assert appears_as_standalone_sentence(
        "안내입니다. 선종은 **1cm** 이상이면 제거를 권합니다.", APPROVED
    )


def _candidate(body: str) -> dict:
    return {
        "title": "선종 안내",
        "body": body,
        "meta_description": "",
        "faq_question": "",
        "faq_answer_summary": "",
    }


OTHER = "저희 병원은 모든 검사 결과를 당일에 설명해 드립니다."


def test_unquoted_citation_of_another_candidate_sentence_is_not_exempt() -> None:
    """quote는 필수 문구인데 지적 문구가 따옴표 없이 다른 문장 전체를 짚으면 면제하지 않는다."""

    body = f"{APPROVED} {OTHER}"
    assert matched_must_use_message(
        quote=APPROVED,
        finding_message=f"필수 문구는 문제없지만 {OTHER.rstrip('.')} 부분은 확인되지 않은 병원 고유 주장입니다.",
        messages=[APPROVED],
        candidate=_candidate(body),
    ) is None
    # 대조군: 필수 문구 자신만 짚거나 짧은 표현만 짚은 지적은 그대로 필수 문구 지적이다.
    for message in (
        f"{APPROVED} 이 문장의 가능성 표현을 완화해야 합니다.",
        "'권합니다'는 표현을 조금 더 부드럽게 쓸 수 있습니다.",
    ):
        assert matched_must_use_message(
            quote=APPROVED,
            finding_message=message,
            messages=[APPROVED],
            candidate=_candidate(body),
        ) == APPROVED
