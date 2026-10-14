"""필수 문구는 원장의 답 문장만 — 설문 안내·요청·질문·조각은 고르지 않는다(2026-10-14 핫픽스).

운영 재현: 신기한속 자동 판 v2가 설문지의 안내문 세 문장을, 세종다온 v5가 조각 문장 "중요하며, …"와
명사구를 필수 문구로 골랐다. 고르는 쪽(`_select_must_use`·공개 소개)과 소비하는 쪽(`must_use_requirement`)이
같은 규칙(`essence_claims.is_not_doctor_statement`)을 쓴다.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.essence import EvidenceNoteType, PhilosophyStatus, SourceType
from app.services.essence_claims import (
    AUTOMATIC_ESSENCE_POLICY_VERSION,
    automatic_version_verbatim_texts,
    is_not_doctor_statement,
)
from app.services.essence_engine import (
    MUST_USE_MODE_NONE,
    MUST_USE_MODE_SOURCE,
    MUST_USE_MODE_SURVEY,
    prune_automatic_candidate,
)
from app.services.must_use_verbatim import must_use_requirement

SINGIHAN_INSTRUCTIONS = [
    "모든 질문은 실제 원장님의 말투로, 풍부하게 답변 부탁드립니다.",
    "내부 인력이 원장님에 대해 최대한 자세히 아는 것이 무척이나 중요합니다.",
    "많이 바쁘시겠지만, 모든 질문은 실제 원장님의 말투로, 풍부하게 답변 부탁드립니다.",
]
SEJONG_FRAGMENTS = [
    "중요하며, 음주, 흡연, 수면습관, 일상생활 관리가 필요합니다.",
    "의사와 직원이 친절한 병원",
    "신경과, 통증의학과, 내과의 진료를 인근 대도시를 가지 않아도 충분히 진료할 수 있는 접근성이 좋은 병원",
]
OTHER_NON_STATEMENTS = [
    "진료 철학을 작성해 주세요.",
    "개원 이유를 기재해 주십시오.",
    "환자에게 가장 중요하게 생각하는 것은 무엇인가요?",
    "환자에게 가장 중요하게 생각하는 것은 무엇입니까",
    "그리고 환자분과 충분히 이야기합니다.",
    "또한 검사 결과는 화면으로 함께 봅니다.",
    "친절한 설명",
]
DOCTOR_STATEMENTS = [
    "산부인과 진료는 민감한 진료인 만큼, 환자분이 편안함을 느낄 수 있도록 하는 것을 우선합니다.",
    "형식적인 검사보다 책임 있는 검사를 하겠습니다.",
    "진료할 때는 그림을 보여 드리거나 검사 결과지에 밑줄을 그어 가며 이해하기 쉽게 설명합니다.",
    "저는 환자분이 이해하실 때까지 몇 번이고 설명드립니다.",
    "검사 결과는 화면으로 함께 봅니다.",
]


@pytest.mark.parametrize("sentence", SINGIHAN_INSTRUCTIONS + SEJONG_FRAGMENTS + OTHER_NON_STATEMENTS)
def test_instructions_questions_and_fragments_are_not_doctor_statements(sentence: str) -> None:
    assert is_not_doctor_statement(sentence) is True


@pytest.mark.parametrize("sentence", DOCTOR_STATEMENTS)
def test_doctor_answers_stay_eligible(sentence: str) -> None:
    assert is_not_doctor_statement(sentence) is False


def _source(source_type, raw_text=""):
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_type=source_type,
        source_metadata={},
        raw_text=raw_text,
        operator_note=None,
        content_hash="hash",
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
        confidence=0.9,
        note_metadata={},
    )


def _payload(**overrides):
    payload = {
        "positioning_statement": None,
        "doctor_voice": None,
        "patient_promise": None,
        "content_principles": [],
        "tone_guidelines": [],
        "must_use_messages": [],
        "avoid_messages": [],
        "treatment_narratives": [],
        "local_context": {},
        "medical_ad_risk_rules": [],
        "evidence_map": {},
        "unsupported_gaps": [],
        "conflict_notes": [],
        "synthesis_notes": None,
    }
    payload.update(overrides)
    return payload


def test_survey_instruction_sentences_are_never_selected_as_must_use() -> None:
    """신기한속 재현: 설문 노트가 안내문뿐이면 설문 방식으로 고르지 않는다."""

    survey = _source(SourceType.INTERVIEW, " ".join(SINGIHAN_INSTRUCTIONS))
    notes = [_note(survey, text) for text in SINGIHAN_INSTRUCTIONS]

    pruned, report = prune_automatic_candidate(
        _payload(must_use_messages=list(SINGIHAN_INSTRUCTIONS)), notes, [survey]
    )

    assert pruned["must_use_messages"] == []
    assert report.must_use["mode"] == MUST_USE_MODE_NONE


def test_survey_answer_wins_over_instruction_in_the_same_note() -> None:
    answer = "저는 환자분이 이해하실 때까지 몇 번이고 설명드립니다."
    survey = _source(SourceType.INTERVIEW, f"{SINGIHAN_INSTRUCTIONS[0]} {answer}")
    note = _note(survey, f"{SINGIHAN_INSTRUCTIONS[0]} {answer}")

    pruned, report = prune_automatic_candidate(_payload(), [note], [survey])

    assert pruned["must_use_messages"] == [answer]
    assert report.must_use["mode"] == MUST_USE_MODE_SURVEY


def test_fragments_fall_back_to_clean_source_sentences() -> None:
    """세종다온 재현: 설문 조각·명사구는 건너뛰고 다른 근거의 완결 문장을 쓴다."""

    survey = _source(SourceType.INTERVIEW, " ".join(SEJONG_FRAGMENTS))
    survey_notes = [_note(survey, text) for text in SEJONG_FRAGMENTS]
    homepage = _source(SourceType.HOMEPAGE)
    home_note = _note(homepage, "검사 전에 충분히 설명합니다. 원장님께서 직접 진료합니다.")

    pruned, report = prune_automatic_candidate(
        _payload(), survey_notes + [home_note], [survey, homepage]
    )

    assert pruned["must_use_messages"] == ["검사 전에 충분히 설명합니다."]
    assert report.must_use["mode"] == MUST_USE_MODE_SOURCE


def _automatic(messages):
    return SimpleNamespace(
        status=PhilosophyStatus.APPROVED,
        must_use_messages=messages,
        synthesis_metadata={
            "mode": "AUTOMATIC",
            "policy_version": AUTOMATIC_ESSENCE_POLICY_VERSION,
            "must_use": {
                "mode": "SURVEY_VERBATIM",
                "items": [{"text": text, "evidence_note_ids": ["n"]} for text in messages],
            },
        },
    )


def test_consumption_drops_stored_instruction_sentences() -> None:
    clean = "형식적인 검사보다 책임 있는 검사를 하겠습니다."
    philosophy = _automatic(SINGIHAN_INSTRUCTIONS + SEJONG_FRAGMENTS + [clean])

    assert automatic_version_verbatim_texts(philosophy) == {clean}
    assert must_use_requirement(philosophy).messages == (clean,)


def test_policy_version_was_bumped_for_the_rebuild() -> None:
    """옛 정책(2026-10-14.3)으로 만든 자동 판은 다음 정기 조정에서 다시 만들어진다."""

    assert AUTOMATIC_ESSENCE_POLICY_VERSION != "2026-10-14.3"
