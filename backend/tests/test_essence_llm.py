"""LLM 기반 essence 추출/합성 검증.

실제 Anthropic API를 호출하지 않고 client.messages.create를 가짜 응답으로 대체한다.
검증 포인트:
- source-processing: source_excerpt가 원문 verbatim일 때만 노트로 저장된다.
- synthesis: 진짜 doctor_voice descriptor + treatment_narrative가 근거 노트에 묶여 나온다.
- ANTHROPIC_API_KEY가 없으면 deterministic 폴백이 그대로 동작한다.
"""
import json
import uuid
from types import SimpleNamespace

import pytest

from app.models.essence import EvidenceNoteType
from app.services import essence_engine
from app.services.essence_engine import (
    process_source_asset,
    synthesize_philosophy,
    validate_philosophy_grounding,
)


class _FakeMessage:
    def __init__(self, text: str):
        self.content = [SimpleNamespace(text=text)]


class _FakeMessages:
    def __init__(self, text: str):
        self._text = text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage(self._text)


class _FakeAnthropic:
    def __init__(self, text: str):
        self.messages = _FakeMessages(text)


@pytest.fixture
def llm_key(monkeypatch):
    monkeypatch.setattr(essence_engine.settings, "ANTHROPIC_API_KEY", "sk-test")


def _patch_client(monkeypatch, text: str) -> _FakeAnthropic:
    fake = _FakeAnthropic(text)
    monkeypatch.setattr(essence_engine, "_anthropic_client", lambda: fake)
    return fake


def test_llm_source_processing_keeps_only_verbatim_excerpts(monkeypatch, llm_key):
    raw_text = (
        "원장님은 치료 전 충분한 설명을 드리는 것을 중요하게 생각합니다. "
        "치질 수술은 환자 상태에 따라 상담 후 결정합니다."
    )
    asset = SimpleNamespace(raw_text=raw_text, operator_note=None)

    llm_payload = json.dumps(
        {
            "evidence_notes": [
                {
                    "note_type": "DOCTOR_PHILOSOPHY",
                    "claim": "원장은 충분한 설명을 중요하게 여긴다.",
                    "source_excerpt": "치료 전 충분한 설명을 드리는 것을 중요하게 생각합니다",
                    "confidence": 0.9,
                    "note_metadata": {"patient_language": ["충분한 설명"]},
                },
                {
                    "note_type": "TREATMENT_SIGNAL",
                    "claim": "치질 수술은 상담 후 결정한다.",
                    "source_excerpt": "치질 수술은 환자 상태에 따라 상담 후 결정합니다",
                    "confidence": 0.8,
                    "note_metadata": {"treatment": "치질 수술"},
                },
                {
                    # 원문에 없는 환각 발췌 — 버려져야 한다.
                    "note_type": "KEY_MESSAGE",
                    "claim": "이 병원은 1등입니다.",
                    "source_excerpt": "저희가 강남 1등 병원입니다",
                    "confidence": 0.5,
                    "note_metadata": {},
                },
            ]
        }
    )
    _patch_client(monkeypatch, llm_payload)

    notes = process_source_asset(asset)

    excerpts = [n.source_excerpt for n in notes]
    # verbatim 노트 2개만 남고 환각 발췌는 제거된다.
    assert "치료 전 충분한 설명을 드리는 것을 중요하게 생각합니다" in excerpts
    assert "치질 수술은 환자 상태에 따라 상담 후 결정합니다" in excerpts
    assert "저희가 강남 1등 병원입니다" not in excerpts
    assert all(n.source_excerpt in raw_text for n in notes)
    assert any(n.note_type == EvidenceNoteType.DOCTOR_PHILOSOPHY for n in notes)
    treatment = next(n for n in notes if n.note_type == EvidenceNoteType.TREATMENT_SIGNAL)
    assert treatment.note_metadata.get("treatment") == "치질 수술"


def test_llm_synthesis_produces_grounded_voice_and_narrative(monkeypatch, llm_key):
    note_voice = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.TONE_SIGNAL,
        source_excerpt="치료 전 충분한 설명을 드립니다.",
        note_metadata={},
    )
    note_treatment = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.TREATMENT_SIGNAL,
        source_excerpt="치질 수술은 상담 후 결정합니다.",
        note_metadata={"treatment": "치질 수술"},
    )
    notes = [note_voice, note_treatment]
    source = SimpleNamespace(id=uuid.uuid4(), processed_at=None, status=None, content_hash="h")

    llm_payload = json.dumps(
        {
            "positioning_statement": {
                "text": "지역 환자가 부담 없이 상담받을 수 있는 병원",
                "evidence_note_ids": [str(note_voice.id)],
            },
            "doctor_voice": {
                "text": "단정적 홍보를 피하고 과정을 차분히 설명하는 1인칭 설명형 문체",
                "evidence_note_ids": [str(note_voice.id)],
            },
            "treatment_narratives": [
                {
                    "treatment": "치질 수술",
                    "patient_language": ["통증", "일상 복귀"],
                    "cautions": ["회복 기간은 개인차가 있습니다."],
                    "evidence_note_ids": [str(note_treatment.id)],
                }
            ],
            # 존재하지 않는 노트 id를 섞어도 필터링되어야 한다.
            "must_use_messages": [
                {"text": "충분한 설명", "evidence_note_ids": [str(uuid.uuid4())]}
            ],
            "synthesis_notes": "근거 기반 합성.",
        }
    )
    _patch_client(monkeypatch, llm_payload)

    payload = synthesize_philosophy(SimpleNamespace(name="장편한외과의원"), [source], notes)

    # 진짜 voice descriptor (regex 추측이 아님).
    assert payload["doctor_voice"] == "단정적 홍보를 피하고 과정을 차분히 설명하는 1인칭 설명형 문체"
    assert payload["evidence_map"]["doctor_voice"] == [str(note_voice.id)]
    # treatment_narrative가 근거 노트에 묶여 있다.
    narrative = payload["treatment_narratives"][0]
    assert narrative["treatment"] == "치질 수술"
    assert narrative["patient_language"] == ["통증", "일상 복귀"]
    assert narrative["evidence_note_ids"] == [str(note_treatment.id)]
    # 환각 note id를 가리키던 must_use_messages는 grounded id가 없으므로 evidence_map에서 빠진다.
    assert "must_use_messages" not in payload["evidence_map"]
    # 합성 결과가 grounding 검증을 통과한다.
    assert validate_philosophy_grounding(payload, notes) == []


def test_llm_synthesis_falls_back_when_response_ungrounded(monkeypatch, llm_key):
    note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.KEY_MESSAGE,
        source_excerpt="충분히 설명합니다.",
        note_metadata={},
    )
    source = SimpleNamespace(id=uuid.uuid4(), processed_at=None, status=None, content_hash="h")
    # 모든 필드가 존재하지 않는 노트만 참조 → grounded 필드 0 → deterministic 폴백.
    llm_payload = json.dumps(
        {
            "positioning_statement": {
                "text": "근거 없는 문구",
                "evidence_note_ids": [str(uuid.uuid4())],
            }
        }
    )
    _patch_client(monkeypatch, llm_payload)

    payload = synthesize_philosophy(SimpleNamespace(name="테스트병원"), [source], [note])

    # deterministic 폴백이 동작해 실제 노트에 묶인 positioning_statement를 만든다.
    assert payload["positioning_statement"]
    assert payload["evidence_map"]["positioning_statement"] == [str(note.id)]
    assert validate_philosophy_grounding(payload, [note]) == []


def test_deterministic_fallback_runs_without_api_key(monkeypatch):
    """ANTHROPIC_API_KEY가 없으면 LLM 클라이언트를 만들지 않고 규칙 기반으로 동작한다."""
    monkeypatch.setattr(essence_engine.settings, "ANTHROPIC_API_KEY", "")

    def _boom():  # LLM 경로로 새면 즉시 실패하도록
        raise AssertionError("키가 없는데 Anthropic 클라이언트를 만들면 안 됩니다.")

    monkeypatch.setattr(essence_engine, "_anthropic_client", _boom)

    asset = SimpleNamespace(
        raw_text="원장님은 충분히 설명하는 진료 원칙을 중요하게 생각합니다.",
        operator_note="최고라는 표현은 사용하지 않습니다.",
    )
    notes = process_source_asset(asset)

    assert notes
    assert all(n.source_excerpt in asset.raw_text or n.source_excerpt in asset.operator_note for n in notes)
    assert any(n.note_type == EvidenceNoteType.DOCTOR_PHILOSOPHY for n in notes)
