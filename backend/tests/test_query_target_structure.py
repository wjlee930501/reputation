"""측정 질의 문장 → AIQueryTarget 구조 필드 복원 테스트.

커버 범위:
- 템플릿별 지역/진료과/질환/시술/환자 의도 복원
- 템플릿을 못 찾은 문장의 폴백(쓰레기 값을 질환으로 저장하지 않음)
- 빈 필드만 채우는 백필(수기 편집 보존)
- FAQ 질문 문장 정규화 / 폴백 임상 키워드 추출
"""
import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from types import SimpleNamespace

import pytest

from app.services.query_target_structure import (
    INTENT_AVAILABILITY,
    INTENT_COMPARISON,
    INTENT_COST,
    INTENT_INFO,
    INTENT_RECOMMENDATION,
    INTENT_SYMPTOM,
    apply_structure_to_target,
    clinical_keyword_from_query,
    describe_query_text,
    natural_patient_question,
)
from app.utils.query_target_backfill import backfill_targets


@pytest.mark.parametrize(
    "query_text,intent,regions,condition,treatment,specialty",
    [
        ("강남역 정형외과 병원 추천해줘", INTENT_RECOMMENDATION, ["강남역"], None, None, "정형외과"),
        ("강남역 정형외과 전문의 추천", INTENT_RECOMMENDATION, ["강남역"], None, None, "정형외과"),
        (
            "강남역 정형외과 병원 어디가 좋은지 비교해줘",
            INTENT_COMPARISON,
            ["강남역"],
            None,
            None,
            "정형외과",
        ),
        (
            "역삼동에서 정형외과 진료 받을 수 있는 병원 알려줘",
            INTENT_AVAILABILITY,
            ["역삼동"],
            None,
            None,
            "정형외과",
        ),
        ("강남역 정형외과 진료비 어느 정도야?", INTENT_COST, ["강남역"], None, None, "정형외과"),
        (
            "허리디스크 진료를 받으려는데 강남역 어느 병원으로 가야 해?",
            INTENT_SYMPTOM,
            ["강남역"],
            "허리디스크",
            None,
            None,
        ),
        (
            "강남역에서 허리디스크 치료하는 병원 알려줘",
            INTENT_SYMPTOM,
            ["강남역"],
            "허리디스크",
            None,
            None,
        ),
        ("역삼동 오십견 진료 가능한 병원", INTENT_AVAILABILITY, ["역삼동"], "오십견", None, None),
        (
            "강남역에서 도수치료 받을 수 있는 병원 알려줘",
            INTENT_AVAILABILITY,
            ["강남역"],
            None,
            "도수치료",
            None,
        ),
        (
            "역삼동 대장내시경 가능한 병원 추천해줘",
            INTENT_RECOMMENDATION,
            ["역삼동"],
            None,
            "대장내시경",
            None,
        ),
        ("치질 초기 증상이 뭔지 알려줘", INTENT_INFO, [], "치질", None, None),
    ],
)
def test_template_queries_restore_structured_fields(
    query_text, intent, regions, condition, treatment, specialty
):
    """sov_engine 템플릿으로 만들어진 질의는 구조를 100% 되찾아야 한다."""
    structure = describe_query_text(query_text)

    assert structure.matched_template is not None
    assert structure.target_intent == intent
    assert structure.region_terms == regions
    assert structure.condition_or_symptom == condition
    assert structure.treatment == treatment
    assert structure.specialty == specialty
    assert structure.is_question is True


def test_clinical_keyword_prefers_condition_then_treatment():
    assert describe_query_text("역삼동 오십견 진료 가능한 병원").clinical_keyword == "오십견"
    assert (
        describe_query_text("역삼동 대장내시경 가능한 병원 추천해줘").clinical_keyword
        == "대장내시경"
    )
    assert describe_query_text("강남역 정형외과 병원 추천해줘").clinical_keyword == "정형외과"


def test_unmatched_text_does_not_become_a_disease_name():
    """폴백이 문장 전체를 질환명으로 저장하면 이후 프롬프트·검증이 그 말을 찾게 된다."""
    structure = describe_query_text("아무 문장이나 좋은 곳")

    assert structure.matched_template is None
    assert structure.condition_or_symptom is None
    assert structure.treatment is None


def test_legacy_free_text_still_recovers_region_and_intent():
    """템플릿 밖 문장(레거시·AE 수기 입력)도 지역과 의도까지는 되짚는다."""
    structure = describe_query_text("강남역 허리디스크 비수술 어디가 좋은지")

    assert structure.matched_template is None
    assert "강남역" in structure.region_terms
    assert structure.target_intent == INTENT_COMPARISON


def test_empty_query_is_safe():
    structure = describe_query_text("")
    assert structure.region_terms == []
    assert structure.is_question is False


# ── 백필 ──────────────────────────────────────────────────────────────────────


def _target(name: str, **overrides):
    base = {
        "name": name,
        "target_intent": "증상 탐색",
        "region_terms": [],
        "specialty": None,
        "condition_or_symptom": None,
        "treatment": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_backfill_fills_empty_fields():
    target = _target("강남역에서 허리디스크 치료하는 병원 알려줘")

    assert apply_structure_to_target(target) is True
    assert target.region_terms == ["강남역"]
    assert target.condition_or_symptom == "허리디스크"
    assert target.target_intent == INTENT_SYMPTOM


def test_backfill_preserves_operator_edits():
    """AE가 Admin에서 넣은 값은 재시드·백필이 덮어쓰지 않는다."""
    target = _target(
        "강남역에서 허리디스크 치료하는 병원 알려줘",
        condition_or_symptom="척추관협착증",
        region_terms=["논현동"],
        target_intent="수술 여부 검토",
    )

    apply_structure_to_target(target)

    assert target.condition_or_symptom == "척추관협착증"
    assert target.region_terms == ["논현동"]
    assert target.target_intent == "수술 여부 검토"


def test_backfill_is_idempotent():
    target = _target("역삼동 대장내시경 가능한 병원 추천해줘")

    assert apply_structure_to_target(target) is True
    assert apply_structure_to_target(target) is False


def test_backfill_targets_counts_only_changed_rows():
    targets = [
        _target("역삼동 오십견 진료 가능한 병원"),
        _target("이미 채워진 질문", condition_or_symptom="치질", region_terms=["위례"]),
    ]

    result = backfill_targets(targets)

    assert result.targets_total == 2
    assert result.targets_updated == 1


# ── 글에 쓰는 표현 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query_text,expected",
    [
        ("강남역 정형외과 병원 추천해줘", "강남역 정형외과 병원 추천해 주시겠어요?"),
        ("역삼동 오십견 진료 가능한 병원", "역삼동 오십견 진료 가능한 병원은 어디인가요?"),
        ("치질 초기 증상이 뭔지 알려줘", "치질 초기 증상이 무엇인가요?"),
        (
            "허리디스크 진료를 받으려는데 강남역 어느 병원으로 가야 해?",
            "허리디스크 진료를 받으려는데 강남역 어느 병원으로 가야 하나요?",
        ),
    ],
)
def test_natural_patient_question_is_polite_and_meaning_preserving(query_text, expected):
    assert natural_patient_question(query_text) == expected


def test_clinical_keyword_fallback_skips_region_and_stopwords():
    """구조 필드가 없을 때도 지역명이 주제어로 잡히면 안 된다."""
    assert (
        clinical_keyword_from_query("강남역에서 허리디스크 치료하는 병원 알려줘", [])
        == "허리디스크"
    )
    assert clinical_keyword_from_query("강남역 병원 추천해줘", ["강남역"]) is None
