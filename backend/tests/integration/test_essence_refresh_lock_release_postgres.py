import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.models.essence import (
    EvidenceNoteType,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital
from app.models.operations import OperationRun
from app.services.essence_auto_review import (
    EssenceAiReview,
    EssenceRefreshStatus,
    refresh_essence_snapshot,
)
from app.services.essence_engine import compute_sources_snapshot_hash
from app.utils.db_locks import acquire_hospital_advisory_lock_sync


def test_slow_provider_holds_no_hospital_lock_and_stale_result_is_discarded(pg_engine) -> None:
    hospital_id = uuid.uuid4()
    source_id = uuid.uuid4()
    note_id = uuid.uuid4()
    with Session(pg_engine, expire_on_commit=False) as seed:
        hospital = Hospital(
            id=hospital_id,
            name="Essence lock proof",
            slug=f"essence-lock-{uuid.uuid4().hex}",
        )
        source = HospitalSourceAsset(
            id=source_id,
            hospital_id=hospital_id,
            source_type=SourceType.INTERVIEW,
            title="원장 인터뷰",
            raw_text="환자에게 충분히 설명합니다.",
            content_hash="input-v1",
            status=SourceStatus.PROCESSED,
            processed_at=datetime.now(timezone.utc),
            source_metadata={},
        )
        note = HospitalSourceEvidenceNote(
            id=note_id,
            hospital_id=hospital_id,
            source_asset_id=source_id,
            note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY,
            claim="충분한 설명",
            source_excerpt="환자에게 충분히 설명합니다.",
            confidence=0.95,
            note_metadata={},
        )
        seed.add_all([hospital, source, note])
        seed.commit()

    provider_entered = Event()
    provider_release = Event()

    def synthesize(_hospital, sources, notes, **_kwargs):
        provider_entered.set()
        assert provider_release.wait(timeout=5)
        return {
            "positioning_statement": "충분한 설명",
            "doctor_voice": None,
            "patient_promise": None,
            "content_principles": [],
            "tone_guidelines": [],
            "must_use_messages": [],
            "avoid_messages": [],
            "treatment_narratives": [],
            "local_context": {},
            "medical_ad_risk_rules": [],
            "evidence_map": {"positioning_statement": [str(notes[0].id)]},
            "source_asset_ids": [str(item.id) for item in sources],
            "unsupported_gaps": [],
            "conflict_notes": [],
            "synthesis_notes": "lock proof",
            "source_snapshot_hash": compute_sources_snapshot_hash(sources),
        }

    def review(_hospital, _previous, _payload, notes):
        return EssenceAiReview(
            decision="APPROVE",
            confidence=0.99,
            findings=(),
            reviewed_evidence_note_ids=tuple(str(note.id) for note in notes),
            summary="reviewed",
            model="test",
        )

    def run_refresh():
        with Session(pg_engine, expire_on_commit=False) as db:
            return refresh_essence_snapshot(
                db,
                hospital_id,
                synthesizer=synthesize,
                reviewer=review,
                claim_token="slow-provider-attempt",
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_refresh)
            assert provider_entered.wait(timeout=5)
            with Session(pg_engine, expire_on_commit=False) as duplicate:
                duplicate_result = refresh_essence_snapshot(
                    duplicate,
                    hospital_id,
                    synthesizer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("a live lease must fence duplicate provider work")
                    ),
                    reviewer=review,
                    claim_token="slow-provider-attempt",
                )
            assert duplicate_result.status == EssenceRefreshStatus.SNAPSHOT_CHANGED
            with Session(pg_engine, expire_on_commit=False) as concurrent:
                concurrent.execute(text("SET LOCAL lock_timeout = '500ms'"))
                # This would raise LockNotAvailable if refresh still held the
                # transaction-scoped hospital lock during the provider wait.
                acquire_hospital_advisory_lock_sync(concurrent, hospital_id)
                changed = concurrent.get(HospitalSourceAsset, source_id)
                changed.raw_text = "외부 호출 중 운영자가 고친 새 원문입니다."
                changed.content_hash = "input-v2"
                changed.status = SourceStatus.PENDING
                changed.processed_at = None
                concurrent.commit()
            provider_release.set()
            result = future.result(timeout=5)

        assert result.status == EssenceRefreshStatus.SNAPSHOT_CHANGED
        assert result.philosophy_id is None
    finally:
        provider_release.set()
        with Session(pg_engine) as cleanup:
            cleanup.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
            hospital = cleanup.get(Hospital, hospital_id)
            if hospital is not None:
                cleanup.delete(hospital)
            cleanup.commit()
