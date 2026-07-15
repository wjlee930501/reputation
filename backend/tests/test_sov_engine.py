import json
from types import SimpleNamespace

import pytest

from app.services import sov_engine


class _FakeChoice:
    def __init__(self, content):
        self.message = SimpleNamespace(content=content)


class _FakeCompletions:
    async def create(self, **kwargs):
        assert kwargs["response_format"] == {"type": "json_object"}
        return SimpleNamespace(
            choices=[
                _FakeChoice(
                    json.dumps(
                        {
                            "competitors": [
                                {"name": "경쟁병원", "is_mentioned": True, "mention_rank": 1},
                            ]
                        }
                    )
                )
            ]
        )


@pytest.mark.asyncio
async def test_parse_competitors_accepts_json_object_wrapper(monkeypatch):
    monkeypatch.setattr(
        sov_engine.openai_client.chat,
        "completions",
        _FakeCompletions(),
    )

    parsed = await sov_engine._parse_competitors(["경쟁병원"], "경쟁병원이 먼저 언급되었습니다.")

    assert parsed == [{"name": "경쟁병원", "is_mentioned": True, "mention_rank": 1}]


# ── calculate_sov: 성공 측정 0건이면 None (측정 안 됨 ≠ 실제 0% 언급) ──


def test_calculate_sov_returns_none_when_no_records():
    assert sov_engine.calculate_sov([]) is None


def test_calculate_sov_returns_none_when_all_failed():
    records = [
        {"is_mentioned": False, "measurement_status": "FAILED"},
        {"is_mentioned": False, "measurement_status": "FAILED"},
    ]
    assert sov_engine.calculate_sov(records) is None


def test_calculate_sov_returns_none_when_all_empty_raw_response():
    # measurement_status 미존재 + raw_response 비어있음 = 네트워크 실패 추정 → 분모 제외
    records = [{"is_mentioned": False, "raw_response": ""}]
    assert sov_engine.calculate_sov(records) is None


def test_calculate_sov_zero_percent_is_distinct_from_none():
    records = [
        {"is_mentioned": False, "measurement_status": "SUCCESS"},
        {"is_mentioned": False, "measurement_status": "SUCCESS"},
    ]
    assert sov_engine.calculate_sov(records) == 0.0


def test_calculate_sov_excludes_failures_from_denominator():
    records = [
        {"is_mentioned": True, "measurement_status": "SUCCESS"},
        {"is_mentioned": False, "measurement_status": "SUCCESS"},
        {"is_mentioned": False, "measurement_status": "FAILED"},
    ]
    # 성공 2건 중 1건 언급 → 50.0 (실패는 분모 제외)
    assert sov_engine.calculate_sov(records) == 50.0


# ── prefilter 정규화: 표기 변형(띄어쓰기)에 강건 ──


def test_normalize_for_prefilter_strips_whitespace_and_symbols():
    assert sov_engine._normalize_for_prefilter("장편한 외과") == "장편한외과"
    assert sov_engine._normalize_for_prefilter("장편한-외과!") == "장편한외과"
    assert sov_engine._normalize_for_prefilter("") == ""


class _MentionedCompletions:
    async def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                _FakeChoice(
                    json.dumps(
                        {
                            "is_mentioned": True,
                            "mention_rank": 1,
                            "sentiment": "positive",
                            "mention_context": "언급됨",
                        }
                    )
                )
            ]
        )


@pytest.mark.asyncio
async def test_parse_mention_does_not_prefilter_out_spacing_variant(monkeypatch):
    # 병원명 "장편한외과" ↔ 응답 "장편한 외과" 처럼 공백만 다른 경우에도 사전 필터가
    # 걸러내지 않고 LLM 판정까지 도달해야 한다.
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _MentionedCompletions())

    parsed = await sov_engine._parse_mention("장편한외과", "이 지역은 장편한 외과가 유명합니다.")

    assert parsed["is_mentioned"] is True


class _SearchResponses:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text="검색 기반 답변")


@pytest.mark.asyncio
async def test_chatgpt_web_search_is_required_not_optional(monkeypatch):
    responses = _SearchResponses()
    monkeypatch.setattr(sov_engine.openai_client, "responses", responses)

    result = await sov_engine._query_chatgpt_with_search("수원 외과 추천")

    assert result == "검색 기반 답변"
    assert responses.kwargs["tools"] == [{"type": "web_search"}]
    assert responses.kwargs["tool_choice"] == "required"
