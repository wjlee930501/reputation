import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.admin.essence import _serialize_philosophy, _serialize_source
from app.models.essence import EvidenceNoteType, PhilosophyStatus, SourceStatus, SourceType


def test_serialize_source_exposes_display_labels():
    source_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    timestamp = datetime(2026, 5, 1, tzinfo=timezone.utc)
    source = SimpleNamespace(
        id=source_id,
        hospital_id=hospital_id,
        source_type=SourceType.INTERVIEW,
        title="원장 인터뷰",
        url="https://example.com/interview",
        raw_text="인터뷰 본문",
        operator_note=None,
        source_metadata={},
        content_hash="hash",
        status=SourceStatus.PROCESSED,
        process_error=None,
        processed_at=timestamp,
        created_by="MotionLabs",
        updated_by=None,
        created_at=timestamp,
        updated_at=timestamp,
        file_url=None,
        mime_type=None,
        file_size_bytes=None,
        is_public=False,
    )

    serialized = _serialize_source(source, evidence_note_count=2)

    assert serialized["display"] == {
        "source_type_label": "원장 인터뷰",
        "status_label": "처리완료",
    }
    assert serialized["source_type"] == SourceType.INTERVIEW
    assert serialized["status"] == SourceStatus.PROCESSED
    assert serialized["evidence_note_count"] == 2
    # 사진 자산 신규 필드도 항상 노출되어야 한다 (기본값 포함).
    assert serialized["file_url"] is None
    assert serialized["mime_type"] is None
    assert serialized["file_size_bytes"] is None
    assert serialized["is_public"] is False
    # evidence_notes를 전달하지 않으면 None (구분: 빈 리스트 vs 미조회).
    assert serialized["evidence_notes"] is None


def test_serialize_source_includes_photo_asset_fields():
    source_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    timestamp = datetime(2026, 5, 1, tzinfo=timezone.utc)
    source = SimpleNamespace(
        id=source_id,
        hospital_id=hospital_id,
        source_type=SourceType.PHOTO_DOCTOR,
        title="원장 사진",
        url=None,
        raw_text=None,
        operator_note=None,
        source_metadata={},
        content_hash="hash",
        status=SourceStatus.PROCESSED,
        process_error=None,
        processed_at=timestamp,
        created_by="MotionLabs",
        updated_by=None,
        created_at=timestamp,
        updated_at=timestamp,
        file_url="/assets/abc/doctor.png",
        mime_type="image/png",
        file_size_bytes=12_345,
        is_public=True,
    )

    serialized = _serialize_source(source)

    assert serialized["source_type"] == SourceType.PHOTO_DOCTOR
    assert serialized["display"]["source_type_label"] == "사진 — 원장"
    assert serialized["file_url"] == "/assets/abc/doctor.png"
    assert serialized["mime_type"] == "image/png"
    assert serialized["file_size_bytes"] == 12_345
    assert serialized["is_public"] is True


def test_serialize_source_emits_evidence_notes_when_provided():
    source_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    timestamp = datetime(2026, 5, 1, tzinfo=timezone.utc)
    source = SimpleNamespace(
        id=source_id,
        hospital_id=hospital_id,
        source_type=SourceType.INTERVIEW,
        title="원장 인터뷰",
        url=None,
        raw_text="환자에게 충분히 설명하는 진료 원칙을 지킵니다.",
        operator_note=None,
        source_metadata={},
        content_hash="hash",
        status=SourceStatus.PROCESSED,
        process_error=None,
        processed_at=timestamp,
        created_by="MotionLabs",
        updated_by=None,
        created_at=timestamp,
        updated_at=timestamp,
        file_url=None,
        mime_type=None,
        file_size_bytes=None,
        is_public=False,
    )

    note = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        source_asset_id=source_id,
        note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY,
        claim="진료 철학 관련 근거 확인: 환자에게 충분히 설명하는 진료 원칙",
        source_excerpt="환자에게 충분히 설명하는 진료 원칙을 지킵니다.",
        excerpt_start=0,
        excerpt_end=20,
        confidence=0.78,
        note_metadata={},
        created_at=timestamp,
    )

    serialized = _serialize_source(source, evidence_note_count=1, evidence_notes=[note])

    assert serialized["evidence_note_count"] == 1
    assert isinstance(serialized["evidence_notes"], list)
    assert len(serialized["evidence_notes"]) == 1
    serialized_note = serialized["evidence_notes"][0]
    assert serialized_note["note_type"] == EvidenceNoteType.DOCTOR_PHILOSOPHY
    assert serialized_note["claim"].startswith("진료 철학")
    assert serialized_note["source_asset_id"] == str(source_id)


def test_serialize_philosophy_exposes_display_labels():
    philosophy_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    timestamp = datetime(2026, 5, 1, tzinfo=timezone.utc)
    philosophy = SimpleNamespace(
        id=philosophy_id,
        hospital_id=hospital_id,
        version=3,
        status=PhilosophyStatus.APPROVED,
        positioning_statement="병원 기준",
        doctor_voice=None,
        patient_promise=None,
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=[],
        avoid_messages=[],
        treatment_narratives=[],
        local_context={},
        medical_ad_risk_rules=[],
        evidence_map={},
        source_asset_ids=[],
        unsupported_gaps=[],
        conflict_notes=[],
        synthesis_notes=None,
        source_snapshot_hash=None,
        created_by="MotionLabs",
        reviewed_by="MotionLabs",
        approved_at=timestamp,
        approval_note=None,
        created_at=timestamp,
        updated_at=timestamp,
    )

    serialized = _serialize_philosophy(philosophy)

    assert serialized["display"] == {"status_label": "승인됨"}
    assert serialized["status"] == PhilosophyStatus.APPROVED
