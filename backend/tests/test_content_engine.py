import json
import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from tenacity import RetryError, stop_after_attempt  # noqa: E402

from app.models.content import ContentType  # noqa: E402
from app.services import content_engine  # noqa: E402
from app.services.content_engine import (  # noqa: E402
    _build_content_brief_context,
    _format_internal_link_target,
    _format_treatment_narrative,
    _normalize_references,
    _parse_json_response,
    _sanitize_forbidden,
    _validate_body_length,
    _validate_geo,
    forbidden_check_text,
)
from app.utils.medical_filter import check_forbidden  # noqa: E402


def test_parse_json_response_accepts_fenced_json():
    raw = """```json
{"title":"제목","body":"본문","meta_description":"요약"}
```"""

    parsed = _parse_json_response(raw, json_module=json)

    assert parsed["title"] == "제목"
    assert parsed["body"] == "본문"


def test_parse_json_response_extracts_surrounded_object():
    raw = 'Here is the JSON:\n{"title":"제목","body":"본문"}\nDone.'

    parsed = _parse_json_response(raw, json_module=json)

    assert parsed == {"title": "제목", "body": "본문"}


def test_validate_body_length_accepts_expert_blog_length():
    _validate_body_length("## 제목\n" + ("본문입니다. " * 360))


def test_validate_body_length_rejects_short_body():
    with pytest.raises(ValueError, match="too short"):
        _validate_body_length("짧은 본문")


def test_validate_body_length_rejects_runaway_body():
    with pytest.raises(ValueError, match="too long"):
        _validate_body_length("긴 본문입니다. " * 900)


def test_forbidden_check_text_includes_faq_fields():
    # P1-2 회귀 가드: FAQPage rich result로 그대로 노출되는 faq_question/faq_answer_summary가
    # 금지 표현 검사 텍스트에서 빠지면 의료광고법 필터를 통째로 우회한다.
    result = {
        "title": "어깨 통증 진료 안내",
        "body": "환자 상태에 따라 진료 방향을 설명합니다.",
        "meta_description": "어깨 통증 진료 안내입니다.",
        "faq_question": "어깨 통증 완치 가능한가요?",
        "faq_answer_summary": "성공률이 높은 치료를 안내합니다.",
    }

    violations = check_forbidden(forbidden_check_text(result))

    assert "완치" in violations
    assert "성공률" in violations


def test_forbidden_check_text_ignores_missing_faq_fields():
    result = {"title": "제목", "body": "본문", "meta_description": None}

    text = forbidden_check_text(result)

    assert "제목" in text and "본문" in text


def test_sanitize_forbidden_cleans_faq_fields():
    # 정제 경로도 FAQ 필드를 포함해야 재검사에서 hard-fail하지 않는다 (P1-2).
    result = {
        "title": "어깨 통증 진료",
        "body": "환자 상태에 따라 설명합니다.",
        "meta_description": "진료 안내",
        "faq_question": "완치 보장되나요?",
        "faq_answer_summary": "1등 병원에서 안내합니다.",
    }
    violations = check_forbidden(forbidden_check_text(result))
    assert violations

    sanitized = _sanitize_forbidden(result, violations)

    assert check_forbidden(forbidden_check_text(sanitized)) == []


def test_sanitize_forbidden_removes_obfuscated_terms():
    # 회귀 가드: NFKC 탐지와 raw 제거가 어긋나 전각/zero-width 위반이 제거되지 않으면
    # generate_content가 hard-fail한다. 정규화 후 제거로 실제로 사라져야 한다.
    result = {
        "title": "１등 진료",          # full-width digit
        "body": "성공 확률 １００％ 달성, 부작용 제로",  # full-width 100%
        "meta_description": "완​치 가능",  # zero-width space in 완치
    }
    violations = check_forbidden(
        result["title"] + result["body"] + result["meta_description"]
    )
    assert violations  # detected

    sanitized = _sanitize_forbidden(result, violations)
    remaining = check_forbidden(
        sanitized["title"] + sanitized["body"] + sanitized["meta_description"]
    )
    assert remaining == [], f"sanitizer left obfuscated violations: {remaining}"


# ── references 정규화 순서 회귀 (P-2: GEO hard-fail이 raw references로 검증되던 버그) ──

def _geo_hospital() -> SimpleNamespace:
    return SimpleNamespace(name="테스트병원", director_name="김원장", region=["강남"])


def test_raw_non_whitelisted_references_bypass_geo_hard_fail():
    """버그 재현: 화이트리스트 밖 URL만 있는 raw references는 비어있지 않아
    _validate_geo가 hard-fail하지 않는다 (수정 전 동작)."""
    raw_refs = [{"title": "출처", "url": "https://not-a-real-authority.example.com/guide"}]
    result = {
        "body": "## 증상\n테스트병원 김원장 강남 1회 안내입니다.\n" + ("본문입니다. " * 100),
        "references": raw_refs,
    }

    findings = _validate_geo(result, _geo_hospital(), ContentType.DISEASE)
    assert isinstance(findings, list)  # ValueError 없이 통과 — 버그 상황 재현


def test_normalized_non_whitelisted_references_trigger_geo_hard_fail():
    """수정된 동작: references를 먼저 정규화(화이트리스트 밖 URL 제거)한 뒤 검증해야
    실제로 GEO hard-fail이 발생해 tenacity 재시도를 강제한다."""
    raw_refs = [{"title": "출처", "url": "https://not-a-real-authority.example.com/guide"}]
    normalized = _normalize_references(raw_refs)
    assert normalized == []  # 화이트리스트 밖 URL은 정규화 단계에서 제거됨

    result = {
        "body": "## 증상\n테스트병원 김원장 강남 1회 안내입니다.\n" + ("본문입니다. " * 100),
        "references": normalized,
    }
    with pytest.raises(ValueError, match="GEO hard-fail"):
        _validate_geo(result, _geo_hospital(), ContentType.DISEASE)


async def test_generate_content_hard_fails_end_to_end_for_non_whitelisted_only_references(
    monkeypatch,
):
    """generate_content 통합 회귀: 화이트리스트 밖 URL만 인용된 응답은 정규화 후
    references가 비어 GEO hard-fail로 재시도되어야 한다 (근거 없이 발행 완료 금지)."""
    hospital = SimpleNamespace(
        name="테스트병원",
        address="서울",
        phone="02-000-0000",
        business_hours="",
        region=["강남"],
        specialties=["정형외과"],
        keywords=["어깨 통증"],
        director_name="김원장",
        director_career="",
        director_philosophy="",
        treatments=[],
    )
    body = (
        "## 증상\n" + ("본문입니다. " * 200) + "\n\n"
        "## 진단\n" + ("본문입니다. " * 200)
    )
    payload = {
        "title": "어깨 통증 진단과 치료",
        "body": body,
        "meta_description": "어깨 통증의 원인과 치료 방향을 안내합니다.",
        "references": [
            {"title": "출처", "url": "https://not-a-real-authority.example.com/guide"}
        ],
        "faq_question": None,
        "faq_answer_summary": None,
    }

    class _FakeResponse:
        content = [SimpleNamespace(text=json.dumps(payload))]

    def fake_create(*_args, **_kwargs):
        return _FakeResponse()

    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(content_engine.client.messages, "create", fake_create)
    monkeypatch.setattr(content_engine.generate_content.retry, "stop", stop_after_attempt(1))
    monkeypatch.setattr(content_engine.generate_content.retry, "sleep", _no_sleep)

    with pytest.raises(RetryError) as exc_info:
        await content_engine.generate_content(hospital, ContentType.DISEASE)

    assert "GEO hard-fail" in str(exc_info.value.last_attempt.exception())


# ── content_brief dict 필드 → 자연어 프롬프트 조립 회귀 (P-4) ──────────────────

def test_format_treatment_narrative_renders_sentence_not_dict_repr():
    value = {
        "source": "approved_philosophy",
        "treatment": "치질 수술",
        "angle": "증상 단계와 회복 계획을 함께 설명합니다.",
        "details": {"treatment": "치질 수술"},
    }
    formatted = _format_treatment_narrative(value)

    assert formatted == "치질 수술 — 증상 단계와 회복 계획을 함께 설명합니다."
    assert "{" not in formatted and "'source'" not in formatted


def test_format_internal_link_target_renders_sentence_not_dict_repr():
    value = {"type": "content_item", "content_id": "abc-123", "path": "/test-clinic/contents/abc-123"}
    formatted = _format_internal_link_target(value)

    assert formatted == "본문에서 자연스러운 위치에 내부 링크로 연결: /test-clinic/contents/abc-123"
    assert "{" not in formatted


def test_build_content_brief_context_excludes_raw_dict_repr():
    content_brief = {
        "target_query": "강남 치질 수술 회복 기간은?",
        "patient_intent": "추천형",
        "treatment_narrative": {
            "source": "hospital_profile",
            "treatment": "치질 수술",
            "angle": "회복 계획을 설명합니다.",
        },
        "internal_link_target": {
            "type": "content_item",
            "content_id": "abc-123",
            "path": "/test-clinic/contents/abc-123",
        },
    }

    context = _build_content_brief_context(content_brief)

    assert "{'source'" not in context
    assert "{'type'" not in context
    assert "치질 수술 — 회복 계획을 설명합니다." in context
    assert "/test-clinic/contents/abc-123" in context
