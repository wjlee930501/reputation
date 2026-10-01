"""금지 표현이 든 승인본 필수 문구는 작가 프롬프트의 필수 목록에서도 빠진다.

원문 그대로 요구하는 집합(`must_use_verbatim`)에서 뺀 문구를 프롬프트는 여전히 "원문 그대로,
빠지면 저장 안 됨"으로 요구하면, 작가는 금지 표현 게이트에 걸릴 문장을 넣느라 재작성 예산을
쓰고 결국 그 문구는 조용히 사라진다. 두 쪽이 같은 집합을 말해야 한다.
가이드(brief)에만 있는 문구는 원문 요구가 아닌 작성 방향 줄로만 남는다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import content_engine
from tests.provider_network_guard_support import (
    forbid_external_network as _forbid_external_network_fixture,  # noqa: F401
)

FORBIDDEN_MESSAGE = "저희 병원은 대장암 완치를 보장합니다."
ALLOWED_MESSAGE = "국립암센터는 50대 이상에게 5~10년 주기의 대장내시경 검사를 권고하고 있습니다."
GUIDE_ONLY = "검사 전날 식단 안내를 함께 전합니다."


@pytest.fixture(autouse=True)
def _no_external_calls(forbid_external_network):
    return forbid_external_network


def _philosophy(must_use: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        id="p-approved",
        status="APPROVED",
        version=4,
        positioning_statement="",
        doctor_voice="",
        patient_promise="",
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=list(must_use),
        avoid_messages=[],
        medical_ad_risk_rules=[],
        treatment_narratives=[],
    )


def _must_use_section(context: str) -> str:
    return context.split("must_use_messages:", 1)[1].split("avoid_messages:", 1)[0]


def test_forbidden_must_use_message_is_not_in_the_writer_prompt() -> None:
    philosophy = _philosophy([FORBIDDEN_MESSAGE, ALLOWED_MESSAGE])
    # 가이드는 승인 당시 목록의 사본이다(content_brief.build_content_brief).
    brief = {"target_query": "대장내시경", "must_use_messages": [FORBIDDEN_MESSAGE, ALLOWED_MESSAGE]}

    philosophy_context = content_engine._build_philosophy_context(philosophy)
    brief_context = content_engine._build_content_brief_context(brief, philosophy)

    assert ALLOWED_MESSAGE in _must_use_section(philosophy_context)
    assert FORBIDDEN_MESSAGE not in philosophy_context
    assert FORBIDDEN_MESSAGE not in brief_context


def test_guide_only_messages_are_not_presented_as_verbatim_requirements() -> None:
    philosophy = _philosophy([ALLOWED_MESSAGE])
    brief = {
        "target_query": "대장내시경",
        "philosophy_reference": {"id": "p-approved", "version": 4},
        "must_use_messages": [ALLOWED_MESSAGE, GUIDE_ONLY],
    }

    brief_context = content_engine._build_content_brief_context(brief, philosophy)
    must_use_line = brief_context.split("must_use_messages:", 1)[1].split("\n", 2)
    # 가이드의 must_use_messages 줄은 승인본 참조뿐이고, 가이드에만 있는 문구는 별도 줄이다.
    assert GUIDE_ONLY not in must_use_line[1]
    assert GUIDE_ONLY in brief_context
    assert "원문 그대로 요구하지 않음" in brief_context
    # 필수 문구 원문 요구 규칙은 승인본 목록만 가리킨다.
    assert "[승인된 콘텐츠 운영 기준]의 must_use_messages" in content_engine.STATIC_SYSTEM_BLOCK


def test_stale_guide_messages_do_not_reach_the_writer_prompt() -> None:
    """승인본이 2cm→1cm로 바뀐 뒤 옛 버전 가이드의 2cm 문장은 작성 방향으로도 싣지 않는다."""

    approved = "용종이 1cm 이상이면 절제를 권합니다."
    withdrawn = "용종이 2cm 이상이면 절제를 권합니다."
    philosophy = _philosophy([approved])
    brief = {
        "target_query": "대장내시경",
        "philosophy_reference": {"id": "p-approved", "version": 3},
        "must_use_messages": [withdrawn],
    }

    brief_context = content_engine._build_content_brief_context(brief, philosophy)
    assert withdrawn not in brief_context
    assert "2cm" not in brief_context
    assert approved in content_engine._build_philosophy_context(philosophy)
