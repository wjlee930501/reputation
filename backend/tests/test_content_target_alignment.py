"""측정 질의가 프롬프트와 검증을 실제로 조향하는지 확인한다.

리뷰 §1.2 C: 예전에는 target_query가 `[승인된 콘텐츠 가이드]`의 한 줄이었고,
유형 프롬프트는 hospital.keywords 전체를 넣었으며, 글이 그 질문에 답했는지 확인하는
검사가 없었다.
"""
import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

import uuid
from types import SimpleNamespace

import pytest

from app.models.content import ContentType
from app.services.content_brief import build_content_brief
from app.services.content_engine import (
    TARGET_KEYWORD_FINDING_PREFIX,
    _fill_type_prompt,
    _validate_seo,
    _validate_target_alignment,
)


def _hospital(**overrides):
    base = {
        "id": uuid.uuid4(),
        "name": "장편한외과의원",
        "director_name": "김장편",
        "region": ["강남", "역삼동"],
        "keywords": ["치질", "탈장", "대장내시경", "하지정맥류"],
        "specialties": ["대장항문외과"],
        "director_philosophy": "정확한 진단",
        "treatments": [{"name": "치질 수술", "description": "당일 수술"}],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _query_target(**overrides):
    base = {
        "id": uuid.uuid4(),
        "name": "강남역에서 허리디스크 치료하는 병원 알려줘",
        "target_intent": "증상 탐색",
        "region_terms": ["강남역"],
        "specialty": None,
        "condition_or_symptom": "허리디스크",
        "treatment": None,
        "priority": "HIGH",
        "target_month": "2026-10",
        "decision_criteria": [],
        "variants": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _brief(query_target=None):
    return build_content_brief(
        hospital=_hospital(),
        content_item=SimpleNamespace(
            id=uuid.uuid4(), title=None, content_type=ContentType.DISEASE
        ),
        query_target=query_target if query_target is not None else _query_target(),
    )


# ── 브리프가 분해해 두는 조향 필드 ────────────────────────────────────────────


def test_brief_exposes_target_keyword_question_and_region():
    brief = _brief()

    assert brief["target_keyword"] == "허리디스크"
    assert brief["target_question"] == "강남역에서 허리디스크 치료하는 병원 알려주시겠어요?"
    assert brief["target_region_terms"] == ["강남역"]


def test_brief_falls_back_to_the_longest_non_region_token():
    """구조 필드가 비어 있는 레거시 타깃도 지역명이 주제어가 되면 안 된다."""
    brief = _brief(
        _query_target(condition_or_symptom=None, treatment=None, specialty=None)
    )

    assert brief["target_keyword"] == "허리디스크"


# ── 프롬프트 조향 ─────────────────────────────────────────────────────────────


def test_faq_prompt_binds_the_measured_question():
    prompt = _fill_type_prompt(ContentType.FAQ, _hospital(), _brief())

    assert "강남역에서 허리디스크 치료하는 병원 알려주시겠어요?" in prompt
    assert "faq_question 필드에는 위 '환자 질문 문장'을 그대로" in prompt
    # 병원 키워드 전체가 아니라 이 질문의 키워드만 들어간다.
    assert "진료 키워드: 허리디스크" in prompt
    assert "하지정맥류" not in prompt


def test_local_prompt_uses_the_measured_region_not_the_whole_profile():
    prompt = _fill_type_prompt(ContentType.LOCAL, _hospital(), _brief())

    assert "지역: 강남역" in prompt
    # 프로파일 region 전체("강남 역삼동")로 렌더링되면 측정 질의와 어긋난다.
    assert "지역: 강남 역삼동" not in prompt


def test_disease_prompt_states_the_question_the_first_paragraph_must_answer():
    prompt = _fill_type_prompt(ContentType.DISEASE, _hospital(), _brief())

    assert "[측정된 환자 질문 — 이 글이 답해야 하는 대상]" in prompt
    assert "핵심 키워드(제목 또는 첫 H2에 반드시 그대로 포함): 허리디스크" in prompt
    assert "첫 문단에서 위 환자 질문에 직접 답하세요" in prompt


def test_column_prompt_is_not_steered_by_the_measured_query():
    """원장 칼럼은 측정 질의가 아니라 원장 목소리가 주제다."""
    prompt = _fill_type_prompt(ContentType.COLUMN, _hospital(), _brief())

    assert "[측정된 환자 질문" not in prompt


def test_prompt_without_a_brief_keeps_the_profile_keywords():
    prompt = _fill_type_prompt(ContentType.DISEASE, _hospital(), None)

    assert "진료 키워드: 치질, 탈장, 대장내시경, 하지정맥류" in prompt
    assert "[측정된 환자 질문" not in prompt


# ── 검증 ─────────────────────────────────────────────────────────────────────


def _result(title="허리디스크 초기 증상과 치료", body=None, faq_question=None):
    return {
        "title": title,
        "body": body
        if body is not None
        else "## 허리디스크 증상\n\n본문입니다.\n\n## 치료\n\n본문입니다.",
        "faq_question": faq_question,
    }


def test_keyword_in_title_passes():
    assert _validate_target_alignment(_result(), _brief()) == []


def test_keyword_only_in_first_h2_passes():
    result = _result(title="허리 통증, 언제 병원에 가야 할까요?")
    assert _validate_target_alignment(result, _brief()) == []


def test_keyword_only_in_faq_question_passes():
    result = _result(
        title="허리 통증 안내",
        body="## 증상\n\n본문\n\n## 치료\n\n본문",
        faq_question="허리디스크는 어떻게 치료하나요?",
    )
    assert _validate_target_alignment(result, _brief()) == []


def test_missing_keyword_is_reported_as_a_finding():
    result = _result(title="허리 통증 안내", body="## 증상\n\n본문\n\n## 치료\n\n본문")

    findings = _validate_target_alignment(result, _brief())

    assert len(findings) == 1
    assert findings[0].startswith(TARGET_KEYWORD_FINDING_PREFIX)
    assert "허리디스크" in findings[0]


def test_no_brief_means_no_alignment_check():
    assert _validate_target_alignment(_result(), None) == []
    assert _validate_target_alignment(_result(), {"target_query": ""}) == []


def test_seo_primary_keyword_is_the_clinical_term_not_the_region():
    """예전에는 target_query 첫 토큰(=지역명)이 primary keyword였다."""
    hospital = _hospital()
    body = (
        f"{hospital.name} 안내입니다. 강남 지역 환자가 많습니다.\n\n"
        "## 증상\n\n본문 70% 수치.\n\n## 치료\n\n- 항목\n"
    )
    result = {
        "title": "허리 통증 안내",
        "body": body,
        "meta_description": "가" * 100,
    }

    findings = _validate_seo(result, hospital, _brief(), ContentType.DISEASE)

    assert any("허리디스크" in finding for finding in findings)
    assert not any("'강남역'" in finding for finding in findings)


@pytest.mark.parametrize("content_type", [ContentType.FAQ, ContentType.LOCAL])
def test_steering_survives_missing_region_terms(content_type):
    """지역 없는 타깃(정보형)도 프롬프트 조립이 깨지지 않는다."""
    brief = _brief(_query_target(region_terms=[], name="치질 초기 증상이 뭔지 알려줘"))
    prompt = _fill_type_prompt(content_type, _hospital(), brief)

    assert prompt
