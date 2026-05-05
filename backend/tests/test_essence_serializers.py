import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.admin.essence import _serialize_philosophy, _serialize_source
from app.models.essence import PhilosophyStatus, SourceStatus, SourceType


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
    )

    serialized = _serialize_source(source, evidence_note_count=2)

    assert serialized["display"] == {
        "source_type_label": "원장 인터뷰",
        "status_label": "처리완료",
    }
    assert serialized["source_type"] == SourceType.INTERVIEW
    assert serialized["status"] == SourceStatus.PROCESSED
    assert serialized["evidence_note_count"] == 2


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
