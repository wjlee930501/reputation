"""필수 문구 원문 보존 판정(`must_use_verbatim`)의 정규화 범위와 일치 규칙.

작가 검증과 독립 검수가 이 한 모듈을 쓴다. 정규화 범위 밖의 차이(쉼표·조사·단어·덧붙인 말)는
모두 다른 문장이다. 공급자·네트워크를 쓰지 않는 순수 함수 테스트다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiFindingTarget,
)
from app.services.must_use_verbatim import (
    appears_as_standalone_sentence,
    matched_must_use_message,
    missing_must_use_messages,
    normalize_verbatim,
    required_must_use_messages,
)
from tests.provider_network_guard_support import (
    forbid_external_network as _forbid_external_network_fixture,  # noqa: F401
)

MUST_USE = (
    "선종은 암이 나타나기 이전의 병변이며, 2cm 이상 크기의 선종은 암으로 진행될 수 있으므로 "
    "조기 발견이 중요합니다."
)


@pytest.fixture(autouse=True)
def _no_external_calls(forbid_external_network):
    return forbid_external_network


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(MUST_USE, id="identical"),
        pytest.param(MUST_USE.rstrip("."), id="no-terminal-period"),
        pytest.param(MUST_USE.replace(".", "。"), id="ideographic-period"),
        pytest.param(MUST_USE.replace(" ", ""), id="no-spaces"),
        pytest.param(MUST_USE.replace(" ", "\n  "), id="newlines-and-indent"),
        pytest.param(MUST_USE.replace("2cm", "２ｃｍ").replace(",", "，"), id="full-width"),
        pytest.param(f"'{MUST_USE}'", id="ascii-quotes"),
        pytest.param(f"“{MUST_USE}”", id="curly-quotes"),
        pytest.param(f"「{MUST_USE}」", id="corner-brackets"),
        pytest.param(f"**{MUST_USE}**", id="markdown-bold"),
        pytest.param(MUST_USE.replace("선종은", "선​종은", 1), id="zero-width"),
    ],
)
def test_normalization_scope_treats_these_as_the_same_sentence(variant: str) -> None:
    assert normalize_verbatim(variant) == normalize_verbatim(MUST_USE)


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(MUST_USE.replace("병변이며,", "병변이며"), id="internal-comma-dropped"),
        pytest.param(MUST_USE.replace("발견이", "발견과 제거가"), id="words-changed"),
        pytest.param(MUST_USE.replace("발견이", "발견은"), id="particle-changed"),
        pytest.param(MUST_USE.replace("2cm", "1cm"), id="number-changed"),
        pytest.param(MUST_USE.replace("2cm", "2mm"), id="unit-changed"),
        pytest.param(MUST_USE.rstrip(".") + "며 반드시 받으세요.", id="appended"),
        pytest.param("국가암정보센터에 따르면 " + MUST_USE, id="prefixed"),
        pytest.param(MUST_USE.split(", ")[1], id="partial"),
    ],
)
def test_normalization_scope_keeps_these_distinct(variant: str) -> None:
    assert normalize_verbatim(variant) != normalize_verbatim(MUST_USE)


def test_standalone_sentence_in_markdown_structures() -> None:
    assert appears_as_standalone_sentence(f"## 용종\n\n앞 문장입니다. {MUST_USE} 뒤 문장입니다.", MUST_USE)
    assert appears_as_standalone_sentence(f"- {MUST_USE}\n- 다른 항목", MUST_USE)
    assert appears_as_standalone_sentence(f"> {MUST_USE}", MUST_USE)
    assert appears_as_standalone_sentence(f"1. {MUST_USE}", MUST_USE)
    assert not appears_as_standalone_sentence(f"앞머리에 따르면 {MUST_USE}", MUST_USE)
    assert not appears_as_standalone_sentence(
        MUST_USE.rstrip(".") + "며 반드시 받으세요.", MUST_USE
    )
    # 소수점은 문장 경계가 아니다.
    decimal = "선종이 2.5cm 이상이면 추적 검사가 필요합니다."
    assert appears_as_standalone_sentence(f"앞 문장입니다. {decimal}", decimal)


def test_multi_sentence_must_use_needs_every_sentence_in_order() -> None:
    message = "첫 번째 문장입니다. 두 번째 문장입니다."
    assert appears_as_standalone_sentence(f"도입입니다. {message} 끝입니다.", message)
    assert not appears_as_standalone_sentence("두 번째 문장입니다. 첫 번째 문장입니다.", message)
    assert not appears_as_standalone_sentence("첫 번째 문장입니다. 사이 문장. 두 번째 문장입니다.", message)


def test_required_messages_come_only_from_the_current_approved_essence() -> None:
    philosophy = SimpleNamespace(
        status="APPROVED",
        must_use_messages=[MUST_USE, " ", "짧은 문구입니다.", f"**{MUST_USE}**"],
    )

    assert required_must_use_messages(philosophy) == [MUST_USE, "짧은 문구입니다."]
    # 승인본이 아니면(초안·보관·없음) 원문 요구도 검수 면제도 없다.
    for status in ("DRAFT", "ARCHIVED", None):
        assert required_must_use_messages(
            SimpleNamespace(status=status, must_use_messages=[MUST_USE])
        ) == []
    assert required_must_use_messages(None) == []


def test_required_messages_skip_ones_the_forbidden_expression_filter_blocks() -> None:
    """금지 표현이 든 필수 문구를 원문 그대로 요구하면 저장·발행 게이트와 영구히 충돌한다."""

    philosophy = SimpleNamespace(
        status="APPROVED", must_use_messages=["저희 병원은 완치를 보장합니다.", MUST_USE]
    )
    assert required_must_use_messages(philosophy) == [MUST_USE]


def test_missing_messages_lists_only_the_absent_ones() -> None:
    other = "국립암센터는 50대 이상에게 5~10년 주기의 대장내시경 검사를 권고하고 있습니다."
    body = f"## 안내\n{other}"
    assert missing_must_use_messages(body, [MUST_USE, other]) == [MUST_USE]
    assert missing_must_use_messages(f"{body}\n{MUST_USE}", [MUST_USE, other]) == []


def _candidate(body: str) -> dict:
    return {"title": "제목", "body": body, "meta_description": "", "faq_question": "", "faq_answer_summary": ""}


def test_match_requires_quote_equal_to_the_message_and_present_standalone() -> None:
    body = f"앞 문장입니다. {MUST_USE}"
    assert matched_must_use_message(
        quote=MUST_USE, finding_message="지적", messages=[MUST_USE], candidate=_candidate(body)
    ) == MUST_USE
    # 인용이 비었거나 일부만이면 일치가 아니다.
    for quote in ("", MUST_USE.split(", ")[1]):
        assert matched_must_use_message(
            quote=quote, finding_message="지적", messages=[MUST_USE], candidate=_candidate(body)
        ) is None
    # 본문에 없으면(검수자가 필수 문구를 인용했지만 본문은 의역) 일치가 아니다.
    assert matched_must_use_message(
        quote=MUST_USE,
        finding_message="지적",
        messages=[MUST_USE],
        candidate=_candidate(MUST_USE.replace("발견이", "발견과 제거가")),
    ) is None


def test_match_rejects_a_finding_that_also_quotes_another_full_sentence() -> None:
    other = "본원은 모든 용종을 빠짐없이 찾아냅니다."
    body = f"{MUST_USE} {other}"
    assert matched_must_use_message(
        quote=MUST_USE,
        finding_message=f"'{MUST_USE}'와 '{other}'가 문제입니다.",
        messages=[MUST_USE],
        candidate=_candidate(body),
    ) is None
    # 짧은 표현 인용이나 본문에 없는 대안 문구는 다른 문장 인용이 아니다.
    assert matched_must_use_message(
        quote=MUST_USE,
        finding_message="'진행될 수 있다'는 표현을 '임상 근거가 있다'로 완화 필요.",
        messages=[MUST_USE],
        candidate=_candidate(body),
    ) == MUST_USE


def test_finding_target_field_is_part_of_the_stored_payload() -> None:
    finding = ContentAiFinding(
        ContentAiFindingSeverity.SOFT,
        ContentAiFindingKind.MEDICAL_SAFETY,
        "지적",
        quote=MUST_USE,
        target=ContentAiFindingTarget.MUST_USE_MESSAGE,
        original_severity=ContentAiFindingSeverity.HARD,
    )
    assert finding.blocks_publication is False
    assert finding.payload() == {
        "severity": "SOFT",
        "kind": "MEDICAL_SAFETY",
        "message": "지적",
        "target": "MUST_USE_MESSAGE",
        "quote": MUST_USE,
        "original_severity": "HARD",
    }
    plain = ContentAiFinding(
        ContentAiFindingSeverity.HARD, ContentAiFindingKind.HOSPITAL_FACT, "사실 지적"
    )
    assert plain.blocks_publication is True
    assert plain.payload()["target"] == "CANDIDATE_TEXT"
    # 방어 심층: 대상이 필수 문구이면 심각도 표기와 무관하게 막지 않는다.
    assert (
        ContentAiFinding(
            ContentAiFindingSeverity.HARD,
            ContentAiFindingKind.MEDICAL_SAFETY,
            "지적",
            target=ContentAiFindingTarget.MUST_USE_MESSAGE,
        ).blocks_publication
        is False
    )
