import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.admin.query_targets import _serialize_target, _serialize_variant, _variant_key
from app.schemas.query_target import AIQueryTargetCreate, AIQueryTargetUpdate, AIQueryVariantCreate


def _variant(**overrides):
    base = dict(
        id=uuid.uuid4(),
        query_target_id=uuid.uuid4(),
        query_text="강남 치질 병원 추천",
        platform="CHATGPT",
        language="ko",
        is_active=True,
        query_matrix_id=None,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_query_target_create_cleans_lists_and_validates_month():
    target = AIQueryTargetCreate(
        name="  강남 치질 추천  ",
        target_intent=" 추천형 ",
        region_terms=["강남", " ", "서초"],
        decision_criteria=["회복 기간", "통증 부담"],
        platforms=["CHATGPT", "", "GEMINI"],
        competitor_names=["경쟁병원"],
        target_month="2026-06",
        variants=[AIQueryVariantCreate(query_text=" 치질 수술 어디가 좋아 ")],
    )

    assert target.name == "강남 치질 추천"
    assert target.target_intent == "추천형"
    assert target.region_terms == ["강남", "서초"]
    assert target.platforms == ["CHATGPT", "GEMINI"]
    assert target.variants[0].query_text == "치질 수술 어디가 좋아"


def test_query_target_rejects_invalid_priority_status_and_month():
    with pytest.raises(ValidationError):
        AIQueryTargetCreate(name="테스트", target_intent="추천형", priority="URGENT")

    with pytest.raises(ValidationError):
        AIQueryTargetUpdate(status="DELETED")

    with pytest.raises(ValidationError):
        AIQueryTargetCreate(name="테스트", target_intent="추천형", target_month="2026-13")


def test_serialize_target_summarizes_variants_and_next_action():
    target_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    linked_query_matrix_id = uuid.uuid4()
    active_variant = _variant(query_target_id=target_id, query_matrix_id=linked_query_matrix_id)
    inactive_variant = _variant(query_target_id=target_id, is_active=False, query_text="비활성 질의")
    target = SimpleNamespace(
        id=target_id,
        hospital_id=hospital_id,
        name="강남 치질 수술 추천",
        target_intent="추천형",
        region_terms=["강남"],
        specialty="대장항문외과",
        condition_or_symptom="치질",
        treatment="치질 수술",
        decision_criteria=["회복 기간"],
        patient_language="ko",
        platforms=["CHATGPT", "GEMINI"],
        competitor_names=["경쟁병원"],
        priority="HIGH",
        status="ACTIVE",
        target_month="2026-06",
        created_by="MotionLabs Ops",
        updated_by="MotionLabs Ops",
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
        variants=[inactive_variant, active_variant],
    )

    serialized = _serialize_target(target)

    assert serialized["id"] == str(target_id)
    assert serialized["summary"]["variant_count"] == 2
    assert serialized["summary"]["active_variant_count"] == 1
    assert serialized["summary"]["linked_query_matrix_count"] == 1
    assert serialized["summary"]["next_action"] == "baseline 측정 대기"
    assert serialized["variants"][0]["query_text"] == active_variant.query_text


def test_serialize_variant_redacts_none_query_matrix_id():
    variant = _variant(query_matrix_id=None)

    serialized = _serialize_variant(variant)

    assert serialized["query_matrix_id"] is None
    assert serialized["query_text"] == "강남 치질 병원 추천"


def test_variant_key_normalizes_edges_without_changing_operator_text():
    assert _variant_key("  강남 치질 병원 추천  ", " CHATGPT ", " ko ") == (
        "강남 치질 병원 추천",
        "CHATGPT",
        "ko",
    )
