import json
import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

import pytest  # noqa: E402

from app.services.content_engine import (  # noqa: E402
    _parse_json_response,
    _sanitize_forbidden,
    _validate_body_length,
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
