import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.essence import EvidenceNoteType, PhilosophyStatus, SourceStatus
from app.services import essence_engine
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    ESSENCE_STATUS_MISSING_APPROVED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    compute_source_content_hash,
    find_error_marker_fields,
    process_source_asset,
    screen_content_against_philosophy,
    synthesize_philosophy,
    validate_philosophy_grounding,
)


@pytest.fixture(autouse=True)
def _force_deterministic(monkeypatch):
    """이 파일의 테스트는 deterministic 폴백 경로를 검증한다 — LLM 키를 비워 네트워크 호출을 막는다."""
    monkeypatch.setattr(essence_engine.settings, "ANTHROPIC_API_KEY", "")


def test_process_source_asset_extracts_only_source_backed_notes():
    asset = SimpleNamespace(
        raw_text="원장님은 충분히 설명하는 진료 원칙을 중요하게 생각합니다. 치질 수술은 상태에 따라 상담 후 결정합니다.",
        operator_note="최고라는 표현은 사용하지 않습니다.",
    )

    notes = process_source_asset(asset)

    assert notes
    assert all(note.source_excerpt in asset.raw_text or note.source_excerpt in asset.operator_note for note in notes)
    assert any(note.note_type == EvidenceNoteType.DOCTOR_PHILOSOPHY for note in notes)
    assert any(note.note_type == EvidenceNoteType.RISK_SIGNAL for note in notes)


def test_synthesize_philosophy_requires_evidence_map_for_non_empty_fields():
    source = SimpleNamespace(
        id=uuid.uuid4(),
        content_hash=compute_source_content_hash("인터뷰", None, "충분히 설명합니다."),
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.KEY_MESSAGE,
        source_excerpt="충분히 설명합니다.",
        note_metadata={},
    )

    payload = synthesize_philosophy(SimpleNamespace(name="테스트병원"), [source], [note])

    assert payload["positioning_statement"]
    assert payload["evidence_map"]["positioning_statement"] == [str(note.id)]
    assert validate_philosophy_grounding(payload, [note]) == []


def test_synthesize_philosophy_keeps_medical_risk_rules_source_backed():
    source = SimpleNamespace(
        id=uuid.uuid4(),
        content_hash=compute_source_content_hash("인터뷰", None, "검증된 치료라고 표현하지 않습니다."),
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.RISK_SIGNAL,
        source_excerpt="검증된 치료라고 표현하지 않습니다.",
        note_metadata={"violations": ["검증된"]},
    )

    payload = synthesize_philosophy(SimpleNamespace(name="테스트병원"), [source], [note])

    assert payload["medical_ad_risk_rules"]
    assert "검증된 치료라고 표현하지 않습니다." in payload["medical_ad_risk_rules"][0]
    assert validate_philosophy_grounding(payload, [note], require_text_support=True) == []


def test_synthesize_philosophy_excludes_error_marker_notes():
    # 차단·오류 페이지 잔재("Title: 403 Forbidden") 노트는 철학 조립에서 제외되고,
    # 정상 노트만 핵심 필드로 반영돼야 한다.
    source = SimpleNamespace(
        id=uuid.uuid4(),
        content_hash=compute_source_content_hash("인터뷰", None, "충분히 설명합니다."),
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    error_note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.KEY_MESSAGE,
        source_excerpt="Title: 403 Forbidden",
        note_metadata={},
    )
    clean_note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.KEY_MESSAGE,
        source_excerpt="충분히 설명합니다.",
        note_metadata={},
    )

    payload = synthesize_philosophy(
        SimpleNamespace(name="테스트병원"), [source], [error_note, clean_note]
    )

    # 오염 노트는 어떤 핵심 필드에도 남지 않는다.
    assert find_error_marker_fields(payload) == []
    assert "403 Forbidden" not in (payload["positioning_statement"] or "")
    assert "충분히 설명합니다." in (payload["positioning_statement"] or "")
    assert str(error_note.id) not in payload["evidence_map"].get("positioning_statement", [])


def test_synthesize_philosophy_drops_field_when_only_error_marker_note():
    # 유일한 근거 노트가 오류 잔재면 핵심 필드는 비고, 초안 방어(find_error_marker_fields)는 통과한다.
    source = SimpleNamespace(
        id=uuid.uuid4(),
        content_hash=compute_source_content_hash("인터뷰", None, "Title: 403 Forbidden"),
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    error_note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.KEY_MESSAGE,
        source_excerpt="Title: 403 Forbidden",
        note_metadata={},
    )

    payload = synthesize_philosophy(SimpleNamespace(name="테스트병원"), [source], [error_note])

    assert payload["positioning_statement"] is None
    assert find_error_marker_fields(payload) == []


def test_find_error_marker_fields_flags_polluted_core_fields():
    # 어떤 경로로든 핵심 필드에 잔재가 남으면 조립 계층 방어가 이를 잡아낸다.
    payload = {
        "positioning_statement": "자료에서 확인된 핵심 메시지: Title: 403 Forbidden",
        "patient_promise": "환자에게 말할 수 있는 약속은 이 근거 범위로 제한: Title: 403 Forbidden",
        "must_use_messages": ["정상 메시지"],
    }
    flagged = find_error_marker_fields(payload)
    assert "positioning_statement" in flagged
    assert "patient_promise" in flagged
    assert "must_use_messages" not in flagged


def test_find_error_marker_fields_ignores_clean_payload():
    payload = {
        "positioning_statement": "근거 중심으로 충분히 설명하는 진료를 지향합니다.",
        "patient_promise": "확인된 정보만 환자에게 안내합니다.",
        "treatment_narratives": [{"treatment": "내시경", "angle": "차분히 설명합니다."}],
    }
    assert find_error_marker_fields(payload) == []


def test_grounding_accepts_synthesized_descriptor_derived_from_real_notes():
    """합성된 descriptor는 verbatim 인용이 아니어도, 실제 노트를 가리키면 grounded로 본다."""
    note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.TONE_SIGNAL,
        source_excerpt="치료 전 충분한 설명을 드립니다.",
        note_metadata={},
    )
    payload = {
        # verbatim 인용이 아니라 도출된 문체 descriptor.
        "doctor_voice": "단정적 홍보를 피하고 과정을 차분히 설명하는 1인칭 설명형 문체",
        "evidence_map": {"doctor_voice": [str(note.id)]},
    }

    # 매핑된 노트가 실제로 존재하므로 grounded — verbatim 포함을 더는 요구하지 않는다.
    assert validate_philosophy_grounding(payload, [note], require_text_support=True) == []


def test_grounding_rejects_field_referencing_unknown_note():
    note = SimpleNamespace(
        id=uuid.uuid4(),
        note_type=EvidenceNoteType.KEY_MESSAGE,
        source_excerpt="충분히 설명합니다.",
        note_metadata={},
    )
    payload = {
        "positioning_statement": "도출된 포지셔닝 문구입니다.",
        "evidence_map": {"positioning_statement": [str(uuid.uuid4())]},  # 존재하지 않는 노트 id
    }

    errors = validate_philosophy_grounding(payload, [note], require_text_support=True)

    assert errors
    assert "positioning_statement" in errors[0]


def test_screen_content_blocks_missing_or_risky_essence_statuses():
    item = SimpleNamespace(
        title="치료 안내",
        body="부작용 없는 치료와 100% 회복을 보장합니다.",
        meta_description=None,
    )

    missing = screen_content_against_philosophy(item, None)
    assert missing.status == ESSENCE_STATUS_MISSING_APPROVED
    assert missing.summary["blocking"] is True

    philosophy = SimpleNamespace(
        id=uuid.uuid4(),
        version=1,
        status=PhilosophyStatus.APPROVED,
        avoid_messages=["검수 필요 표현 또는 약속: 과장 표현"],
    )
    risky = screen_content_against_philosophy(item, philosophy)
    assert risky.status == ESSENCE_STATUS_NEEDS_REVIEW
    assert risky.summary["blocking"] is True

    safe_item = SimpleNamespace(
        title="상담 안내",
        body="증상과 생활 패턴을 확인한 뒤 필요한 치료 선택지를 설명합니다.",
        meta_description="진료 전 상담에서 상태를 확인합니다.",
    )
    aligned = screen_content_against_philosophy(safe_item, philosophy)
    assert aligned.status == ESSENCE_STATUS_ALIGNED
    assert aligned.summary["blocking"] is False
