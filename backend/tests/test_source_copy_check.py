"""자료 문장 그대로 옮김 검사(결정 대기 15번 A) — 막지 않고 한 번 다시 쓰게 한 뒤 기록만 남긴다."""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models.content import ContentItem, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital
from app.services import content_engine
from app.services.content_ai_review import ContentAiReview, ContentAiReviewStatus
from app.services.content_generation_review import (
    ContentReviewDependencies,
    GenerationReviewLimits,
    generate_reviewed_content,
)
from app.services.cost_guard import CostGuardDecision
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, EssenceScreeningResult
from app.services.source_copy_check import (
    SOURCE_COPY_FINDING_PREFIX,
    SOURCE_COPY_SUMMARY_KEY,
    copied_source_sentences,
    source_copy_remediation,
)

SURVEY_SENTENCE = "처음 오신 분께는 검사 결과를 화면으로 함께 보면서 생활에서 바꿀 점을 하나씩 정해 드립니다."
MUST_USE = "증상이 가볍더라도 혼자 판단하지 말고 진료를 받아 보시기를 권합니다."


def _philosophy(hospital_id, **fields) -> HospitalContentPhilosophy:
    return HospitalContentPhilosophy(
        hospital_id=hospital_id,
        status=PhilosophyStatus.APPROVED,
        version=3,
        **fields,
    )


@pytest.fixture
def hospital() -> Hospital:
    return Hospital(id=uuid4(), name="격리 검증 의원", director_name="김원장")


def test_sentence_copied_from_fallback_doctor_voice_is_found(hospital: Hospital) -> None:
    philosophy = _philosophy(
        hospital.id, doctor_voice=f"자료 표현 기준 문체: {SURVEY_SENTENCE}"
    )
    body = f"## 진료 흐름\n격리 검증 의원은 첫 방문을 중요하게 생각합니다. {SURVEY_SENTENCE}\n"
    copies = copied_source_sentences(body, hospital, philosophy, [])
    assert [copy.field for copy in copies] == ["doctor_voice"]
    assert copies[0].sentence == SURVEY_SENTENCE


def test_partial_copy_inside_a_longer_sentence_is_found(hospital: Hospital) -> None:
    hospital.director_philosophy = SURVEY_SENTENCE
    body = f"원장은 늘 이렇게 말합니다 — {SURVEY_SENTENCE[:-1]}고 설명합니다."
    copies = copied_source_sentences(body, hospital, None, [])
    assert [copy.field for copy in copies] == ["director_philosophy"]


def test_must_use_sentence_is_never_flagged(hospital: Hospital) -> None:
    philosophy = _philosophy(
        hospital.id, patient_promise=MUST_USE, must_use_messages=[MUST_USE]
    )
    body = f"## 안내\n{MUST_USE}\n"
    assert copied_source_sentences(body, hospital, philosophy, [MUST_USE]) == []


def test_rewritten_meaning_and_short_shared_phrases_are_not_flagged(hospital: Hospital) -> None:
    philosophy = _philosophy(
        hospital.id,
        doctor_voice=SURVEY_SENTENCE,
        content_principles=["정확한 진단이 필요합니다"],
    )
    body = (
        "## 첫 진료\n"
        "첫 진료에서는 검사 결과를 함께 확인하고, 생활 습관 중 먼저 바꿀 부분을 상의합니다. "
        "정확한 진단이 필요합니다. 격리 검증 의원 김원장이 직접 설명합니다."
    )
    assert copied_source_sentences(body, hospital, philosophy, []) == []


def test_safety_and_tone_fields_are_not_sources(hospital: Hospital) -> None:
    caution = "약을 임의로 중단하거나 용량을 바꾸지 말고 반드시 담당 의사와 먼저 상의하시기 바랍니다."
    philosophy = _philosophy(
        hospital.id,
        tone_guidelines=[caution],
        avoid_messages=[caution],
        treatment_narratives=[{"treatment": "고혈압", "cautions": [caution]}],
    )
    assert copied_source_sentences(caution, hospital, philosophy, []) == []


def test_treatment_patient_language_is_a_source(hospital: Hospital) -> None:
    philosophy = _philosophy(
        hospital.id,
        treatment_narratives=[{"treatment": "위내시경", "patient_language": [SURVEY_SENTENCE]}],
    )
    copies = copied_source_sentences(SURVEY_SENTENCE, hospital, philosophy, [])
    assert [copy.field for copy in copies] == ["treatment_narratives"]


def test_remediation_names_the_sentence_and_keeps_must_use() -> None:
    from app.services.source_copy_check import SourceCopy

    [line] = source_copy_remediation([SourceCopy(SURVEY_SENTENCE, "doctor_voice")])
    assert line.startswith(SOURCE_COPY_FINDING_PREFIX)
    assert "must_use_messages" in line and "새 사실" in line
    assert SURVEY_SENTENCE[:20] in line


def test_writer_rule_lives_in_the_static_block_not_the_fingerprint(hospital: Hospital) -> None:
    """지침은 병원별 입력이 아니라 정적 시스템 블록에 있다 — 배포가 작가 입력 지문을 바꾸지 않는다."""

    assert "그 문장을 본문에 그대로 옮기지 말고" in content_engine.STATIC_SYSTEM_BLOCK
    philosophy = _philosophy(hospital.id, doctor_voice=SURVEY_SENTENCE)
    context = content_engine._build_philosophy_context(philosophy)
    assert "그대로 옮기지 말고" not in context


# ── 생성 검수 세션: 막지 않고 한 번 다시 쓰게 한 뒤 기록만 ──

LIMITS = GenerationReviewLimits(3, 2, 1, 1)


def _session(hospital: Hospital, candidates: list[dict[str, Any]]):
    philosophy = _philosophy(hospital.id, doctor_voice=SURVEY_SENTENCE)
    effects = ContentReviewDependencies(
        generate=AsyncMock(side_effect=[deepcopy(c) for c in candidates]),
        review=AsyncMock(
            return_value=ContentAiReview(ContentAiReviewStatus.PASS, 0.95, (), "ok", "m")
        ),
        screen=Mock(return_value=EssenceScreeningResult(ESSENCE_STATUS_ALIGNED, {})),
        check_cost=AsyncMock(return_value=CostGuardDecision(True)),
    )
    context = {
        "hospital": hospital,
        "item": ContentItem(hospital_id=hospital.id, content_type=ContentType.HEALTH),
        "existing_titles": [],
        "philosophy": philosophy,
        "approved_brief": None,
    }
    return effects, context


COPIED = {"title": "첫 진료 안내", "body": f"## 첫 진료\n{SURVEY_SENTENCE}"}
REWRITTEN = {
    "title": "첫 진료 안내",
    "body": "## 첫 진료\n검사 결과를 함께 보며 생활에서 먼저 바꿀 점을 정합니다.",
}


async def test_copied_sentence_spends_the_one_soft_rewrite(hospital: Hospital) -> None:
    effects, context = _session(hospital, [COPIED, REWRITTEN])
    candidate, screening = await generate_reviewed_content(
        **context, dependencies=effects, limits=LIMITS
    )
    assert candidate["body"] == REWRITTEN["body"]
    assert effects.generate.await_count == 2
    findings = effects.generate.await_args_list[1].kwargs["remediation_findings"]
    assert findings[0].startswith(SOURCE_COPY_FINDING_PREFIX)
    assert SOURCE_COPY_SUMMARY_KEY not in screening.summary
    assert screening.status == ESSENCE_STATUS_ALIGNED


async def test_copy_left_after_the_rewrite_is_recorded_not_blocked(hospital: Hospital) -> None:
    effects, context = _session(hospital, [COPIED, COPIED])
    candidate, screening = await generate_reviewed_content(
        **context, dependencies=effects, limits=LIMITS
    )
    assert candidate["body"] == COPIED["body"]
    assert effects.generate.await_count == 2  # 보완 예산은 한 번뿐이다
    assert screening.status == ESSENCE_STATUS_ALIGNED
    assert screening.summary[SOURCE_COPY_SUMMARY_KEY] == [
        {"sentence": SURVEY_SENTENCE[:40] + "…", "field": "doctor_voice"}
    ]
    assert "blocking" not in screening.summary
