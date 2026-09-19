"""강제 도구 호출로 받은 JSON은 따옴표 이스케이프에 의존하지 않는다.

운영 회귀(2026-09-15): 작가 모델이 ```json fence 안에 본문을 넣고, 본문의 한국어
인용문에 이스케이프되지 않은 큰따옴표를 그대로 남겨 `json.loads`가 실패했다.
완성된 글 한 편이 통째로 버려지고 재시도가 생성을 다시 구매했다.
"""
import json
import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from app.models.content import ContentType  # noqa: E402
from app.services import content_ai_review, content_engine  # noqa: E402

# 실패를 일으켰던 바로 그 모양: 본문 안의 원시 큰따옴표.
BODY_WITH_RAW_QUOTES = (
    "## 검진 안내\n진료실에서 환자분들과 이야기를 나누다 보면 "
    '"증상이 없는데 굳이 검진을 받아야 하나요?"라는 질문을 자주 듣습니다. '
    "테스트병원 김원장은 강남에서 충분히 설명합니다. " + ("본문 문장입니다. " * 280)
)


def _tool_use_block(name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=payload)


def _hospital() -> SimpleNamespace:
    return SimpleNamespace(
        name="테스트병원",
        address="서울 강남구",
        phone="02-000-0000",
        business_hours={},
        region=["강남"],
        specialties=["외과"],
        keywords=["복통"],
        director_name="김원장",
        director_career="외과 전문의",
        director_philosophy="충분히 설명합니다.",
        treatments=[],
    )


def _article_payload() -> dict:
    return {
        "title": "복통 진료 전 확인할 점",
        "body": BODY_WITH_RAW_QUOTES,
        "meta_description": "검진 전에 확인할 점을 정리했습니다.",
        "references": [],
        "faq_question": None,
        "faq_answer_summary": None,
    }


@pytest.fixture
def _no_cost(monkeypatch):
    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.services.cost_guard.record_provider_call", noop)


# ── 작가 ──────────────────────────────────────────────────────────


async def test_writer_tool_use_input_survives_unescaped_quotes(monkeypatch, _no_cost):
    payload = _article_payload()
    captured: dict = {}

    def fake_create(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use_block(content_engine.ARTICLE_TOOL_NAME, payload)],
        )

    monkeypatch.setattr(content_engine.client.chat.completions, "create", fake_create)

    saved = await content_engine.generate_content(_hospital(), ContentType.NOTICE)

    assert saved["body"] == BODY_WITH_RAW_QUOTES
    assert saved["title"] == payload["title"]
    # 도구 호출이 강제됐는지(= 모델이 텍스트로 답할 여지가 없는지) 확인한다.
    assert captured["tool_choice"] == content_engine.openrouter.forced_tool_choice(
        content_engine.ARTICLE_TOOL_NAME
    )
    assert captured["tools"] == [
        content_engine.openrouter.function_tool(
            name=content_engine.ARTICLE_TOOL_NAME,
            description=content_engine.ARTICLE_TOOL["description"],
            input_schema=content_engine.ARTICLE_TOOL["input_schema"],
        )
    ]


def test_article_tool_schema_matches_parser_fields():
    schema = content_engine.ARTICLE_TOOL["input_schema"]
    assert set(schema["properties"]) == {
        "title",
        "body",
        "meta_description",
        "references",
        "faq_question",
        "faq_answer_summary",
    }


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
async def test_writer_truncation_is_reported_before_extraction(
    monkeypatch, _no_cost, stop_reason
):
    def fake_create(*_args, **_kwargs):
        return SimpleNamespace(
            stop_reason=stop_reason,
            content=[_tool_use_block(content_engine.ARTICLE_TOOL_NAME, {"title": "잘림"})],
        )

    monkeypatch.setattr(content_engine.client.chat.completions, "create", fake_create)

    with pytest.raises(content_engine.TruncatedProviderOutputError):
        await content_engine._generate_content_attempt(_hospital(), ContentType.NOTICE)


def test_writer_text_path_still_strips_fences():
    payload = _article_payload()
    response = SimpleNamespace(
        content=[SimpleNamespace(text=f"```json\n{json.dumps(payload)}\n```")]
    )

    assert content_engine._extract_generated_result(response) == payload


def test_writer_text_path_rejects_raw_quotes_without_repair():
    """보루는 fence만 벗긴다. 따옴표를 추측해 고치지 않는다."""

    broken = '```json\n{"title": "제목", "body": "환자분이 "왜요?"라고 묻습니다"}\n```'
    response = SimpleNamespace(content=[SimpleNamespace(text=broken)])

    with pytest.raises(ValueError, match="invalid JSON"):
        content_engine._extract_generated_result(response)


# ── 독립 검수 ──────────────────────────────────────────────────────


def test_review_tool_use_input_survives_unescaped_quotes():
    payload = {
        "decision": "REVISE",
        "confidence": 0.9,
        "findings": [
            {
                "severity": "HARD",
                "kind": "MEDICAL_SAFETY",
                "message": '본문의 "완치됩니다"라는 표현을 고쳐야 합니다.',
            }
        ],
        "summary": "안전 지적 1건",
    }
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[_tool_use_block(content_ai_review.REVIEW_TOOL_NAME, payload)],
    )

    review = content_ai_review._review_from_response(response, model="test-model")

    assert review.status == content_ai_review.ContentAiReviewStatus.REVISE
    assert review.findings[0].message == payload["findings"][0]["message"]
    assert review.summary == "안전 지적 1건"


def test_review_text_path_still_parses_fenced_json():
    payload = {
        "decision": "PASS",
        "confidence": 0.95,
        "findings": [],
        "summary": "이상 없음",
    }
    response = SimpleNamespace(
        content=[SimpleNamespace(text=f"```json\n{json.dumps(payload)}\n```")]
    )

    review = content_ai_review._review_from_response(response, model="test-model")

    assert review.status == content_ai_review.ContentAiReviewStatus.PASS


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
async def test_review_truncation_is_unavailable_instead_of_pass(monkeypatch, stop_reason):
    """잘린 도구 입력은 findings가 비어 PASS로 읽힌다 — 안전 게이트를 그렇게 열 수 없다."""

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.services.cost_guard.record_provider_call", noop)
    monkeypatch.setattr("app.services.cost_guard.settle_reservation", noop)
    monkeypatch.setattr("app.services.provider_usage.record_attempt", noop)

    # 확신도만 높고 지적이 아직 안 실린, 그대로라면 PASS가 될 입력.
    truncated = SimpleNamespace(
        id="msg_truncated",
        stop_reason=stop_reason,
        usage=None,
        content=[
            _tool_use_block(
                content_ai_review.REVIEW_TOOL_NAME,
                {"decision": "PASS", "confidence": 0.99, "findings": [], "summary": "이상"},
            )
        ],
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: truncated)
        )
    )

    review = await content_ai_review._provider_review(
        client=client,
        payload="{}",
        model="test-model",
        hospital=_hospital(),
        content={"title": "제목", "body": "본문"},
        decision=SimpleNamespace(receipt=None),
        logical_call_id="call-1",
        attempt_id="attempt-1",
        http_attempt=1,
    )

    assert review.status == content_ai_review.ContentAiReviewStatus.UNAVAILABLE
    assert review.unavailable_reason == (
        content_ai_review.ContentAiReviewUnavailableReason.INVALID_RESPONSE
    )
    assert review.provider_attempted is True


def test_review_tool_schema_matches_parser_fields():
    schema = content_ai_review.REVIEW_TOOL["input_schema"]
    assert set(schema["properties"]) == {"decision", "confidence", "findings", "summary"}
