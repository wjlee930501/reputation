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
from app.utils.medical_filter import check_forbidden


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
    fake = _patch_client(monkeypatch, llm_payload)

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
    assert fake.messages.calls[0]["output_config"]["format"]["type"] == "json_schema"


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
    fake = _patch_client(monkeypatch, llm_payload)

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
    output_format = fake.messages.calls[0]["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    assert output_format["schema"]["additionalProperties"] is False


def test_json_provider_call_retries_empty_response(monkeypatch, llm_key):
    class SequenceMessages:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            text = "" if self.calls == 1 else '{"ok": true}'
            return SimpleNamespace(
                content=[SimpleNamespace(text=text)],
                stop_reason="end_turn",
            )

    messages = SequenceMessages()
    monkeypatch.setattr(
        essence_engine,
        "_anthropic_client",
        lambda: SimpleNamespace(messages=messages),
    )

    result = essence_engine._call_anthropic_json("system", "data", max_tokens=100)

    assert result == {"ok": True}
    assert messages.calls == 2


def test_json_provider_call_honors_single_attempt_budget(monkeypatch, llm_key):
    class EmptyMessages:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(content=[SimpleNamespace(text="")], stop_reason="end_turn")

    messages = EmptyMessages()
    monkeypatch.setattr(
        essence_engine,
        "_anthropic_client",
        lambda: SimpleNamespace(messages=messages),
    )

    with pytest.raises(ValueError, match="no text JSON block"):
        essence_engine._call_anthropic_json(
            "system",
            "data",
            max_tokens=100,
            attempts=1,
        )

    assert messages.calls == 1


def test_synthesis_path_falls_back_after_one_provider_attempt(monkeypatch, llm_key):
    class EmptyMessages:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(content=[SimpleNamespace(text="")], stop_reason="end_turn")

    messages = EmptyMessages()
    monkeypatch.setattr(
        essence_engine,
        "_anthropic_client",
        lambda: SimpleNamespace(messages=messages),
    )
    note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.KEY_MESSAGE,
        source_excerpt="환자 상태를 확인하고 필요한 진료 과정을 설명합니다.",
        note_metadata={},
    )
    source = SimpleNamespace(id=uuid.uuid4(), processed_at=None, status=None, content_hash="h")

    payload = synthesize_philosophy(SimpleNamespace(name="테스트병원"), [source], [note])

    assert messages.calls == 1
    assert payload["positioning_statement"]
    assert payload["evidence_map"]["positioning_statement"] == [str(note.id)]


def test_compact_structured_entries_expand_to_grounded_payload(monkeypatch, llm_key):
    note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.TREATMENT_SIGNAL,
        source_excerpt="치질 수술은 상담 후 결정합니다.",
        note_metadata={"treatment": "치질 수술"},
    )
    source = SimpleNamespace(id=uuid.uuid4(), processed_at=None, status=None, content_hash="h")
    llm_payload = json.dumps(
        {
            "entries": [
                {
                    "kind": "positioning_statement",
                    "text": "충분한 상담을 바탕으로 치료 선택지를 설명합니다.",
                    "detail": "",
                    "patient_language": [],
                    "cautions": [],
                    "evidence_note_ids": [str(note.id)],
                },
                {
                    "kind": "treatment_narrative",
                    "text": "치질 수술",
                    "detail": "",
                    "patient_language": ["상담 후 결정"],
                    "cautions": ["치료 결과를 단정하지 않습니다."],
                    "evidence_note_ids": [str(note.id)],
                },
                {
                    "kind": "synthesis_note",
                    "text": "근거 기반 합성",
                    "detail": "",
                    "patient_language": [],
                    "cautions": [],
                    "evidence_note_ids": [],
                },
            ]
        }
    )
    fake = _patch_client(monkeypatch, llm_payload)

    payload = synthesize_philosophy(SimpleNamespace(name="테스트병원"), [source], [note])

    assert payload["positioning_statement"].startswith("충분한 상담")
    assert payload["treatment_narratives"][0]["treatment"] == "치질 수술"
    assert payload["evidence_map"]["positioning_statement"] == [str(note.id)]
    schema = fake.messages.calls[0]["output_config"]["format"]["schema"]
    assert list(schema["properties"]) == ["entries"]
    assert "maxItems" not in schema["properties"]["entries"]
    assert fake.messages.calls[0]["max_tokens"] == 5000
    assert fake.messages.calls[0]["timeout"] == 90.0
    assert "최대 14개 entry" in fake.messages.calls[0]["system"]


def test_llm_synthesis_missing_cautions_uses_safe_default(monkeypatch, llm_key):
    note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.TREATMENT_SIGNAL,
        source_excerpt="치질 수술은 상담 후 결정합니다.",
        note_metadata={"treatment": "치질 수술"},
    )
    source = SimpleNamespace(id=uuid.uuid4(), processed_at=None, status=None, content_hash="h")
    llm_payload = json.dumps(
        {
            "treatment_narratives": [
                {
                    "treatment": "치질 수술",
                    "patient_language": ["상담 후 결정"],
                    "evidence_note_ids": [str(note.id)],
                }
            ],
            "synthesis_notes": "근거 기반 합성.",
        }
    )
    _patch_client(monkeypatch, llm_payload)

    payload = synthesize_philosophy(SimpleNamespace(name="테스트병원"), [source], [note])
    narrative = payload["treatment_narratives"][0]

    assert narrative["cautions"] == ["치료 결과를 단정하거나 보장하지 않습니다."]
    assert check_forbidden(str(narrative)) == []


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


def test_json_provider_call_does_not_retry_deterministic_client_error(monkeypatch, llm_key):
    """결정적 4xx는 재시도해도 같은 실패다 — 유료 호출이 3배로 늘면 안 된다."""
    import anthropic
    import httpx

    http_response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )

    class BadRequestMessages:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            raise anthropic.BadRequestError(
                "invalid request", response=http_response, body=None
            )

    messages = BadRequestMessages()
    monkeypatch.setattr(
        essence_engine,
        "_anthropic_client",
        lambda: SimpleNamespace(messages=messages),
    )

    with pytest.raises(anthropic.BadRequestError):
        essence_engine._call_anthropic_json("system", "data", max_tokens=100)

    assert messages.calls == 1


def test_json_provider_call_still_retries_transient_provider_error(monkeypatch, llm_key):
    class FlakyMessages:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("upstream 503")
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"ok": true}')], stop_reason="end_turn"
            )

    messages = FlakyMessages()
    monkeypatch.setattr(
        essence_engine,
        "_anthropic_client",
        lambda: SimpleNamespace(messages=messages),
    )

    assert essence_engine._call_anthropic_json("system", "data", max_tokens=100) == {"ok": True}
    assert messages.calls == 2


def test_anthropic_client_is_reused_across_calls(monkeypatch, llm_key):
    """호출마다 클라이언트를 새로 만들면 커넥션 풀과 TLS 세션을 매번 버린다."""
    created: list[dict] = []

    class FakeAnthropicClient:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(essence_engine.anthropic, "Anthropic", FakeAnthropicClient)
    essence_engine._reset_clients_for_tests()

    first = essence_engine._anthropic_client()
    second = essence_engine._anthropic_client()

    assert first is second
    assert len(created) == 1
    assert created[0]["max_retries"] == 0
