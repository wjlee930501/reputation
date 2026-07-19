import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.admin.query_targets import (
    _build_target_operational_summaries,
    _serialize_target,
    _serialize_variant,
    _variant_key,
)
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


def test_query_target_accepts_only_supported_platforms_and_normalizes_case():
    target = AIQueryTargetCreate(
        name="테스트",
        target_intent="추천형",
        platforms=[" chatgpt ", "GEMINI", "CHATGPT"],
        variants=[AIQueryVariantCreate(query_text="질문", platform="gemini")],
    )

    assert target.platforms == ["CHATGPT", "GEMINI"]
    assert target.variants[0].platform == "GEMINI"

    with pytest.raises(ValidationError):
        AIQueryTargetCreate(name="테스트", target_intent="추천형", platforms=["PERPLEXITY"])
    with pytest.raises(ValidationError):
        AIQueryVariantCreate(query_text="질문", platform="GOOGLE")


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
    assert serialized["summary"]["next_action"] == "첫 AI 언급률 측정 대기"
    assert serialized["display"] == {
        "priority_label": "높음",
        "status_label": "운영중",
        "platform_labels": ["ChatGPT", "Gemini"],
    }
    assert serialized["variants"][0]["query_text"] == active_variant.query_text
    assert serialized["variants"][0]["display"] == {"platform_label": "ChatGPT", "status_label": "운영중"}


def test_serialize_target_hides_unsupported_legacy_platforms_and_variants():
    target_id = uuid.uuid4()
    supported = _variant(query_target_id=target_id, platform="CHATGPT")
    unsupported = _variant(query_target_id=target_id, platform="PERPLEXITY")
    target = SimpleNamespace(
        id=target_id,
        hospital_id=uuid.uuid4(),
        name="레거시 질문",
        target_intent="추천형",
        region_terms=[],
        specialty=None,
        condition_or_symptom=None,
        treatment=None,
        decision_criteria=[],
        patient_language="ko",
        platforms=["CHATGPT", "PERPLEXITY"],
        competitor_names=[],
        priority="NORMAL",
        status="ACTIVE",
        target_month=None,
        created_by=None,
        updated_by=None,
        created_at=None,
        updated_at=None,
        variants=[supported, unsupported],
    )

    payload = _serialize_target(target)

    assert payload["platforms"] == ["CHATGPT"]
    assert payload["display"]["platform_labels"] == ["ChatGPT"]
    assert [variant["platform"] for variant in payload["variants"]] == ["CHATGPT"]
    assert payload["summary"]["variant_count"] == 1


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


def test_target_operational_summary_uses_latest_measurement_gap_and_action():
    target_id = uuid.uuid4()
    query_id = uuid.uuid4()
    target = SimpleNamespace(
        id=target_id,
        variants=[_variant(query_target_id=target_id, query_matrix_id=query_id)],
    )
    old_run = uuid.uuid4()
    latest_run = uuid.uuid4()
    records = [
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id=query_id,
            measurement_run_id=old_run,
            measurement_status="SUCCESS",
            is_mentioned=False,
            raw_response="old",
            measured_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id=query_id,
            measurement_run_id=latest_run,
            measurement_status="SUCCESS",
            is_mentioned=True,
            raw_response="latest 1",
            measured_at=datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id=query_id,
            measurement_run_id=latest_run,
            measurement_status="SUCCESS",
            is_mentioned=False,
            raw_response="latest 2",
            measured_at=datetime(2026, 7, 2, 9, 1, tzinfo=timezone.utc),
        ),
    ]
    gaps = [SimpleNamespace(query_target_id=target_id, status="OPEN", severity="HIGH")]
    actions = [
        SimpleNamespace(
            query_target_id=target_id,
            status="IN_PROGRESS",
            title="환자 질문 연계 콘텐츠 보강",
            due_month="2026-08",
            created_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
            gap=SimpleNamespace(severity="HIGH"),
            action_type="CONTENT",
            query_target=SimpleNamespace(priority="HIGH"),
        )
    ]

    summaries = _build_target_operational_summaries([target], records, gaps, actions)

    assert summaries[target_id] == {
        "latest_sov_pct": 50.0,
        "last_measured_at": "2026-07-02T09:01:00+00:00",
        "gap_status": "OPEN",
        "next_action": "환자 질문 연계 콘텐츠 보강",
    }


def test_target_operational_summary_excludes_empty_explicit_success():
    target_id = uuid.uuid4()
    query_id = uuid.uuid4()
    run_id = uuid.uuid4()
    target = SimpleNamespace(
        id=target_id,
        variants=[_variant(query_target_id=target_id, query_matrix_id=query_id)],
    )
    records = [
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id=query_id,
            measurement_run_id=run_id,
            measurement_status="SUCCESS",
            is_mentioned=True,
            raw_response="병원 언급",
            measured_at=datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id=query_id,
            measurement_run_id=run_id,
            measurement_status="SUCCESS",
            is_mentioned=False,
            raw_response="",
            measured_at=datetime(2026, 7, 2, 9, 1, tzinfo=timezone.utc),
        ),
    ]

    summaries = _build_target_operational_summaries([target], records, [], [])

    assert summaries[target_id]["latest_sov_pct"] == 100.0
