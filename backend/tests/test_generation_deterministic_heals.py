"""E2(2026-10-13): 결정적으로 고칠 수 있는 거절은 작가를 다시 사지 않고 같은 회차에서 고친다.

- 본문 문장에만 든 금지 표현 → 그 문장을 통째로 지운다(고쳐 쓰지 않는다).
- 빠진 승인 필수 문구 → 본문 끝에 원문 그대로 덧붙인다.
- 빠진 병원명 → 승인된 병원명 한 줄을 덧붙인다(원장명 치유와 같은 방식).
- 검사 이름 뒤 낱말 '치료' 제목 → 그 낱말만 지운다.

제목·메타·FAQ의 금지 표현, 문장 하나로 짚을 수 없는 위반, 지울 문장이 너무 많은 응답은 종전대로
직전 사유를 넘겨 다시 쓰게 한다. 공급자 호출은 모두 가짜다.
"""

from __future__ import annotations

from tests.test_content_engine_must_use_verbatim import (
    MUST_USE_2CM,
    MUST_USE_SCREENING,
    _generate,
    _install_writer,
    _payload,
    _Writer,
)


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
