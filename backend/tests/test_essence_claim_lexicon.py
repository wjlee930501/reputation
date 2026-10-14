"""의료 주장 어휘 정본의 표기 변형(Codex 3차 P1)과 자동 판 필수 문구의 소비 단계 재검증."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.essence import EvidenceNoteType, PhilosophyStatus, SourceType
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    _must_use_exemption_allowed,
)
from app.services.essence_claims import (
    AUTOMATIC_ESSENCE_POLICY_VERSION,
    has_critical_terms,
    is_medical_claim,
)
from app.services.essence_engine import apply_mandatory_safety_policy, prune_automatic_candidate
from app.services.must_use_verbatim import EXCLUDED_AUTOMATIC_POLICY, must_use_requirement

VARIANTS = [
    "본원의 시술은 통증이 없습니다.",
    "부작용이 없는 시술입니다.",
    "통증 없이 치료합니다.",
    "무통 시술로 진행합니다.",
    "부작용 걱정 없는 치료입니다.",
    "1회로 끝나는 시술입니다.",
    "한 번으로 끝나는 치료입니다.",
    "흉터 없는 수술입니다.",
    "회복 기간 없는 시술입니다.",
    "Our treatment is pain-free.",
    "Painless injections for everyone.",
    "Zero side effects reported.",
    "Done in a single session.",
    "One session is all you need.",
    "No downtime after the procedure.",
    "Results are guaranteed.",
]
CLEAN = [
    "진료 전에 충분히 설명합니다.",
    "검사 결과는 화면으로 함께 봅니다.",
    "치료 계획은 함께 정하고 무리한 권유를 하지 않습니다.",
]


@pytest.mark.parametrize("sentence", VARIANTS)
def test_spacing_particle_and_english_variants_are_medical_claims(sentence: str) -> None:
    assert is_medical_claim(sentence)
    assert has_critical_terms(None, sentence)


@pytest.mark.parametrize("sentence", CLEAN)
def test_process_and_attitude_sentences_stay_clean(sentence: str) -> None:
    assert not is_medical_claim(sentence)


def _source(source_type):
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_type=source_type,
        source_metadata={},
        raw_text="",
        operator_note=None,
        content_hash="h",
        status="PROCESSED",
        processed_at=None,
    )


def _note(source, excerpt, note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY):
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_asset_id=source.id,
        note_type=note_type,
        source_excerpt=excerpt,
        claim=excerpt,
        note_metadata={},
    )


@pytest.mark.parametrize("sentence", VARIANTS)
def test_verbatim_variant_never_becomes_must_use_or_public_about(sentence: str) -> None:
    """원문에 그대로 있어도 의료 주장 꼴이면 필수 문구·공개 소개에 들어가지 않는다."""

    survey = _source(SourceType.INTERVIEW)
    note = _note(survey, sentence)
    payload = apply_mandatory_safety_policy(
        {
            "positioning_statement": sentence,
            "patient_promise": sentence,
            "must_use_messages": [sentence],
            "evidence_map": {
                "positioning_statement": [str(note.id)],
                "patient_promise": [str(note.id)],
                "must_use_messages": [str(note.id)],
            },
        }
    )

    pruned, report = prune_automatic_candidate(payload, [note], [survey])

    assert pruned["must_use_messages"] == []
    assert report.public_about == []


@pytest.mark.parametrize("sentence", VARIANTS)
def test_must_use_exemption_never_applies_to_a_variant_quote(sentence: str) -> None:
    finding = ContentAiFinding(
        ContentAiFindingSeverity.HARD,
        ContentAiFindingKind.HOSPITAL_FACT,
        "승인 자료에서 확인되지 않습니다.",
        quote=sentence,
    )
    assert _must_use_exemption_allowed(finding) is False


def _automatic(messages, *, items=None, policy=AUTOMATIC_ESSENCE_POLICY_VERSION, mode="SURVEY_VERBATIM"):
    return SimpleNamespace(
        status=PhilosophyStatus.APPROVED,
        must_use_messages=messages,
        synthesis_metadata={
            "mode": "AUTOMATIC",
            "policy_version": policy,
            "must_use": {
                "mode": mode,
                "items": [{"text": text, "evidence_note_ids": ["n"]} for text in (messages if items is None else items)],
            },
        },
    )


def test_consumption_revalidates_automatic_must_use_against_the_current_policy() -> None:
    """재현(Codex 3차 P1): 저장된 모델 문장 "…암세포를 제거합니다."는 소비 단계에서 빠진다."""

    claim = "본원의 주사 치료는 암세포를 제거합니다."
    clean = "진료 전에 충분히 설명합니다."
    # 원문 대조 기록에 있어도 의료 주장 어휘면 뺀다.
    requirement = must_use_requirement(_automatic([claim, clean]))
    assert requirement.messages == (clean,)
    assert [item.reason for item in requirement.excluded] == [EXCLUDED_AUTOMATIC_POLICY]
    # 원문 대조 기록에 없는 문구(모델이 쓴 문장)는 뺀다.
    assert must_use_requirement(_automatic([clean], items=[])).messages == ()
    # 옛 정책 판·원문 방식이 아닌 판의 문구는 다음 정기 조정까지 쓰지 않는다.
    assert must_use_requirement(_automatic([clean], policy="2026-10-14.1")).messages == ()
    assert must_use_requirement(_automatic([clean], mode="SYNTHESIZED_GROUNDED")).messages == ()


def test_legacy_approved_must_use_is_consumed_as_before() -> None:
    legacy = SimpleNamespace(
        status=PhilosophyStatus.APPROVED,
        must_use_messages=["1cm 이상 크기의 선종은 진행될 수 있습니다."],
        synthesis_metadata={},
    )
    assert must_use_requirement(legacy).messages == ("1cm 이상 크기의 선종은 진행될 수 있습니다.",)
