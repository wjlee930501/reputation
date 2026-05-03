import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.essence import EvidenceNoteType, PhilosophyStatus, SourceStatus
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    ESSENCE_STATUS_MISSING_APPROVED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    compute_source_content_hash,
    process_source_asset,
    screen_content_against_philosophy,
    synthesize_philosophy,
    validate_philosophy_grounding,
)


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
