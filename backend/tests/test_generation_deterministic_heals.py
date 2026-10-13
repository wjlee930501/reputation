"""E2(2026-10-13): 결정적으로 고칠 수 있는 거절은 작가를 다시 사지 않고 같은 회차에서 고친다.

- 본문 문장에만 든 금지 표현 → 그 문장을 통째로 지운다(고쳐 쓰지 않는다).
- 빠진 승인 필수 문구 → 본문 끝에 원문 그대로 덧붙인다.
- 빠진 병원명 → 승인된 병원명 한 줄을 덧붙인다(원장명 치유와 같은 방식).
- 검사 이름 뒤 낱말 '치료' 제목 → 그 낱말만 지운다.

제목·메타·FAQ의 금지 표현, 문장 하나로 짚을 수 없는 위반, 지울 문장이 너무 많은 응답은 종전대로
직전 사유를 넘겨 다시 쓰게 한다. 공급자 호출은 모두 가짜다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import content_engine
from app.services.question_wellformed import drop_treatment_after_exam
from tests.test_content_engine_must_use_verbatim import (
    MUST_USE_2CM,
    MUST_USE_SCREENING,
    _generate,
    _install_writer,
    _payload,
    _Writer,
)

AD_SENTENCE = "저희 병원은 최고의 의료진이 진료합니다."


async def test_a_forbidden_sentence_in_the_body_is_deleted_without_a_rewrite(monkeypatch):
    writer = _Writer([_payload(f"검사 전날 식단을 조절합니다. {AD_SENTENCE} 물을 충분히 드세요.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 1
    assert AD_SENTENCE not in saved["body"]
    assert "검사 전날 식단을 조절합니다. 물을 충분히 드세요." in saved["body"]


async def test_a_forbidden_title_still_buys_a_complete_rewrite(monkeypatch):
    bad = _payload("검사 전날 식단을 조절합니다.")
    bad["title"] = "최고의 대장내시경 준비 안내"
    writer = _Writer([bad, _payload("검사 전날 식단을 조절합니다.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 2
    assert saved["title"] == "대장내시경 검사 전 준비 안내"


async def test_too_many_forbidden_sentences_are_not_deleted(monkeypatch):
    promotional = " ".join(
        [AD_SENTENCE, "부작용 없는 검사입니다.", "100% 안전합니다.", "국내 최초 장비입니다."]
    )
    writer = _Writer([_payload(promotional), _payload("검사 전날 식단을 조절합니다.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 2
    assert content_engine.FORBIDDEN_SENTENCE_HEAL_LIMIT == 3
    assert AD_SENTENCE not in saved["body"]


async def test_a_missing_must_use_message_is_appended_verbatim(monkeypatch):
    writer = _Writer([_payload(MUST_USE_SCREENING)])
    _install_writer(monkeypatch, writer)

    saved = await _generate([MUST_USE_2CM, MUST_USE_SCREENING])

    assert len(writer.calls) == 1
    assert saved["body"].rstrip().endswith(MUST_USE_2CM)
    assert saved["body"].count(MUST_USE_SCREENING) == 1


async def test_a_missing_hospital_name_is_appended(monkeypatch):
    payload = _payload("검사 전날 식단을 조절합니다.")
    payload["body"] = payload["body"].replace("테스트의원 ", "")
    writer = _Writer([payload])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 1
    assert saved["body"].rstrip().endswith("이 글은 테스트의원에서 안내합니다.")


def test_heal_registry_covers_every_deterministic_error():
    assert set(content_engine._DETERMINISTIC_HEALS) == {
        content_engine.DirectorNameMissingError,
        content_engine.HospitalNameMissingError,
        content_engine.ForbiddenExpressionError,
        content_engine.MissingMustUseMessagesError,
    }
    # 분류기는 종전 문구로 사유를 가른다 — 새 예외도 같은 메시지 머리를 쓴다.
    error = content_engine.ForbiddenExpressionError(
        "Forbidden medical expressions require complete regeneration: ['최고']",
        SimpleNamespace(),
        body_only=True,
    )
    assert isinstance(error, ValueError)


async def test_deletion_that_drops_the_body_below_the_floor_is_not_applied(monkeypatch):
    short = _payload("")
    short["body"] = "## 안내\n테스트의원 김의사 원장이 노원에서 안내합니다. " + AD_SENTENCE + "\n\n## 주의\n짧습니다."
    writer = _Writer([short, _payload("검사 전날 식단을 조절합니다.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 2  # 하한 아래로 줄이는 삭제 대신 작가가 다시 썼다
    assert "Forbidden medical expressions" in writer.calls[1]["messages"][1]["content"]
    assert AD_SENTENCE not in saved["body"]


async def test_an_exam_title_with_a_standalone_treatment_word_is_fixed_before_review(monkeypatch):
    payload = _payload("검사 전날 식단을 조절합니다.")
    payload["title"] = "대장내시경 치료 전 준비 안내"
    writer = _Writer([payload])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 1
    assert saved["title"] == "대장내시경 전 준비 안내"


@pytest.mark.parametrize(
    ("title", "fixed"),
    [
        ("갑상선초음파 치료 비용, 얼마나 드나요?", "갑상선초음파 비용, 얼마나 드나요?"),
        ("대장내시경 치료 전 준비 안내", "대장내시경 전 준비 안내"),
        ("심장 MRI 치료 안내", "심장 MRI 안내"),
        # 조사·어미가 붙은 꼴은 결정적으로 고치지 않는다(작가 보완 재작성 몫).
        ("위내시경 치료가 필요한 경우", None),
        ("갑상선초음파 치료비 안내", None),
        # 시술은 치료와 자연스럽게 붙는다 — 고칠 것이 없다.
        ("임플란트 치료 비용 안내", None),
        ("허리 디스크 치료 방법", None),
    ],
)
def test_drop_treatment_after_exam_only_touches_a_standalone_word(title, fixed):
    assert drop_treatment_after_exam(title) == fixed
