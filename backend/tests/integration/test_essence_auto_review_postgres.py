"""Real-Postgres proof for atomic AI refresh of an approved Essence snapshot."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.essence import (
    EvidenceNoteType,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.essence_auto_review import (
    EssenceAiReview,
    EssenceRefreshStatus,
    refresh_essence_snapshot,
)
from app.services.essence_engine import compute_sources_snapshot_hash


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()


def _seed_baseline(pg_session, *, label: str):
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"{label} 병원",
        slug=f"essence-{label}-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        site_live=False,
    )
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="원장 인터뷰",
        raw_text="진료 전에 충분히 설명하고 환자마다 다른 선택지를 안내합니다.",
        content_hash=f"{label}-source-hash",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    note = HospitalSourceEvidenceNote(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_asset_id=source.id,
        note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY,
        claim="충분한 설명을 중시한다.",
        source_excerpt="진료 전에 충분히 설명하고 환자마다 다른 선택지를 안내합니다.",
        confidence=0.95,
        note_metadata={},
    )
    previous = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
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
        source_snapshot_hash="previous-snapshot",
    )
    pg_session.add_all([hospital, source, note, previous])
    pg_session.flush()
    return hospital, source, note, previous


def _candidate_payload(source, note) -> dict:
    return {
        "positioning_statement": "충분한 설명과 개인별 선택지 안내",
        "doctor_voice": None,
        "patient_promise": None,
        "content_principles": [],
        "tone_guidelines": [],
        "must_use_messages": [],
        "avoid_messages": [],
        "treatment_narratives": [],
        "local_context": {},
        "medical_ad_risk_rules": [],
        "evidence_map": {"positioning_statement": [str(note.id)]},
        "source_asset_ids": [str(source.id)],
        "unsupported_gaps": [],
        "conflict_notes": [],
        "synthesis_notes": "integration test",
        "source_snapshot_hash": compute_sources_snapshot_hash([source]),
    }


def test_clean_snapshot_atomically_archives_previous_and_is_idempotent(pg_session) -> None:
    hospital = Hospital(
        id=uuid.uuid4(),
        name="AI 운영 기준 통합테스트 병원",
        slug=f"essence-auto-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        site_live=False,
    )
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="원장 인터뷰",
        raw_text="진료 전에 충분히 설명하고 환자마다 다른 선택지를 안내합니다.",
        content_hash="new-source-hash",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    note = HospitalSourceEvidenceNote(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_asset_id=source.id,
        note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY,
        claim="충분한 설명을 중시한다.",
        source_excerpt="진료 전에 충분히 설명하고 환자마다 다른 선택지를 안내합니다.",
        confidence=0.95,
        note_metadata={},
    )
    previous = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        positioning_statement=None,
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
        source_snapshot_hash="previous-snapshot",
    )
    pg_session.add_all([hospital, source, note, previous])
    pg_session.flush()

    def synthesize(_hospital, sources, notes, operator_note=None):
        assert operator_note is None
        assert [item.id for item in sources] == [source.id]
        assert [item.id for item in notes] == [note.id]
        return {
            "positioning_statement": "충분한 설명과 개인별 선택지 안내",
            "doctor_voice": None,
            "patient_promise": None,
            "content_principles": [],
            "tone_guidelines": [],
            "must_use_messages": [],
            "avoid_messages": [],
            "treatment_narratives": [],
            "local_context": {},
            "medical_ad_risk_rules": [],
            "evidence_map": {"positioning_statement": [str(note.id)]},
            "source_asset_ids": [str(source.id)],
            "unsupported_gaps": [],
            "conflict_notes": [],
            "synthesis_notes": "integration test",
            "source_snapshot_hash": compute_sources_snapshot_hash(sources),
        }

    def approve(_hospital, _previous, _candidate, _notes):
        return EssenceAiReview(
            decision="APPROVE",
            confidence=0.98,
            findings=(),
            reviewed_evidence_note_ids=(str(note.id),),
            summary="전체 근거 확인",
            model="reviewer-test",
        )

    first = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=synthesize,
        reviewer=approve,
    )

    assert first.status == EssenceRefreshStatus.AUTO_APPROVED
    versions = list(
        pg_session.scalars(
            select(HospitalContentPhilosophy)
            .where(HospitalContentPhilosophy.hospital_id == hospital.id)
            .order_by(HospitalContentPhilosophy.version)
        )
    )
    assert [(item.version, item.status) for item in versions] == [
        (1, PhilosophyStatus.ARCHIVED),
        (2, PhilosophyStatus.APPROVED),
    ]

    second = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("up-to-date replay must not synthesize")
        ),
        reviewer=approve,
    )

    assert second.status == EssenceRefreshStatus.UP_TO_DATE
    approved_count = len(
        list(
            pg_session.scalars(
                select(HospitalContentPhilosophy).where(
                    HospitalContentPhilosophy.hospital_id == hospital.id,
                    HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
                )
            )
        )
    )
    assert approved_count == 1


def test_low_confidence_escalation_preserves_approval_and_reuses_one_draft(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="escalation")
    hospital_id = hospital.id
    synth_calls = 0

    def synthesize(*_args, **_kwargs):
        nonlocal synth_calls
        synth_calls += 1
        return _candidate_payload(source, note)

    def uncertain(*_args, **_kwargs):
        return EssenceAiReview(
            decision="APPROVE",
            confidence=0.70,
            findings=(),
            reviewed_evidence_note_ids=(str(note.id),),
            summary="확신 부족",
            model="reviewer-test",
        )

    first = refresh_essence_snapshot(
        pg_session,
        hospital_id,
        synthesizer=synthesize,
        reviewer=uncertain,
    )
    second = refresh_essence_snapshot(
        pg_session,
        hospital_id,
        synthesizer=synthesize,
        reviewer=uncertain,
    )

    assert first.status == EssenceRefreshStatus.ESCALATED
    assert second.status == EssenceRefreshStatus.ESCALATED
    assert synth_calls == 1
    records = list(
        pg_session.scalars(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.hospital_id == hospital_id
            )
        )
    )
    assert sum(item.status == PhilosophyStatus.APPROVED for item in records) == 1
    assert sum(item.status == PhilosophyStatus.DRAFT for item in records) == 1
    assert (
        pg_session.get(HospitalContentPhilosophy, previous.id).status == PhilosophyStatus.APPROVED
    )


def test_reviewer_exception_creates_no_draft_and_preserves_previous_approval(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="provider-error")

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("review provider unavailable")

    with pytest.raises(RuntimeError, match="review provider unavailable"):
        refresh_essence_snapshot(
            pg_session,
            hospital.id,
            synthesizer=lambda *_args, **_kwargs: _candidate_payload(source, note),
            reviewer=unavailable,
        )

    records = list(
        pg_session.scalars(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.hospital_id == hospital.id
            )
        )
    )
    assert [(item.id, item.status) for item in records] == [
        (previous.id, PhilosophyStatus.APPROVED)
    ]


def test_draft_created_during_review_is_never_approved_with_another_payload(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="draft-race")
    external_draft_id = uuid.uuid4()

    def create_competing_draft(*_args, **_kwargs):
        payload = _candidate_payload(source, note)
        payload["positioning_statement"] = "사람이 별도로 작성한 미검수 문안"
        pg_session.add(
            HospitalContentPhilosophy(
                id=external_draft_id,
                hospital_id=hospital.id,
                version=2,
                status=PhilosophyStatus.DRAFT,
                created_by="OPERATOR",
                **payload,
            )
        )
        pg_session.flush()
        return EssenceAiReview(
            decision="APPROVE",
            confidence=0.99,
            findings=(),
            reviewed_evidence_note_ids=(str(note.id),),
            summary="원래 AI 후보만 검수함",
            model="reviewer-test",
        )

    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: _candidate_payload(source, note),
        reviewer=create_competing_draft,
    )

    assert result.status == EssenceRefreshStatus.ESCALATED
    assert result.philosophy_id == external_draft_id
    assert "별도 초안" in result.findings[0]
    assert (
        pg_session.get(HospitalContentPhilosophy, previous.id).status == PhilosophyStatus.APPROVED
    )
    assert (
        pg_session.get(HospitalContentPhilosophy, external_draft_id).status
        == PhilosophyStatus.DRAFT
    )


def test_source_change_during_review_aborts_promotion(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="source-race")
    hospital_id = hospital.id
    previous_id = previous.id
    pg_session.commit()

    def mutate_source(*_args, **_kwargs):
        source.content_hash = "changed-during-review"
        pg_session.flush()
        return EssenceAiReview(
            decision="APPROVE",
            confidence=0.99,
            findings=(),
            reviewed_evidence_note_ids=(str(note.id),),
            summary="검수 직후 자료가 바뀜",
            model="reviewer-test",
        )

    result = refresh_essence_snapshot(
        pg_session,
        hospital_id,
        synthesizer=lambda *_args, **_kwargs: _candidate_payload(source, note),
        reviewer=mutate_source,
    )

    assert result.status == EssenceRefreshStatus.SNAPSHOT_CHANGED
    records = list(
        pg_session.scalars(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.hospital_id == hospital_id
            )
        )
    )
    assert [(item.id, item.status) for item in records] == [
        (previous_id, PhilosophyStatus.APPROVED)
    ]
