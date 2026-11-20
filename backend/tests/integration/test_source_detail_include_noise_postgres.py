"""자료 상세의 `include_noise` — 제외 표시한 노트를 화면이 다시 볼 수 있어야 되돌릴 수 있다(2026-11-20)."""

import uuid
from datetime import datetime, timezone

from app.api.admin.essence import get_source
from app.models.essence import (
    EvidenceNoteType,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus


async def _seed(db):
    hospital = Hospital(
        id=uuid.uuid4(),
        name="노트표시 병원",
        slug=f"note-detail-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        site_live=False,
    )
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="홈페이지",
        raw_text="진료 전에 충분히 설명합니다.",
        content_hash=f"hash-{uuid.uuid4().hex[:8]}",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )

    def note(is_noise: bool) -> HospitalSourceEvidenceNote:
        return HospitalSourceEvidenceNote(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            source_asset_id=source.id,
            note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY,
            claim="충분한 설명을 중시한다.",
            source_excerpt="진료 전에 충분히 설명합니다.",
            confidence=0.9,
            note_metadata={"is_noise": True} if is_noise else {},
        )

    kept, noisy = note(False), note(True)
    db.add(hospital)
    await db.flush()
    db.add(source)
    await db.flush()
    db.add_all([kept, noisy])
    await db.commit()
    return hospital, source, kept, noisy


async def test_default_detail_still_hides_noise_notes(pg_async_session):
    hospital, source, kept, _noisy = await _seed(pg_async_session)
    body = await get_source(hospital.id, source.id, include_noise=False, db=pg_async_session)
    assert [note["id"] for note in body["evidence_notes"]] == [str(kept.id)]
    assert body["evidence_note_count"] == 1


async def test_include_noise_returns_marked_notes_but_counts_only_used_ones(pg_async_session):
    hospital, source, kept, noisy = await _seed(pg_async_session)
    body = await get_source(hospital.id, source.id, include_noise=True, db=pg_async_session)
    assert {note["id"] for note in body["evidence_notes"]} == {str(kept.id), str(noisy.id)}
    assert body["evidence_note_count"] == 1


async def test_source_whose_notes_are_all_marked_stays_reachable(pg_async_session):
    """노트를 전부 제외 표시해도 목록이 그 수를 알려 화면이 펼칠 수 있다."""
    from app.api.admin.essence import list_sources

    hospital, source, kept, noisy = await _seed(pg_async_session)
    kept.note_metadata = {"is_noise": True}
    await pg_async_session.commit()
    rows = await list_sources(hospital.id, status_filter=None, source_type=None, db=pg_async_session)
    row = next(item for item in rows if item["id"] == str(source.id))
    assert row["evidence_note_count"] == 0
    assert row["noise_note_count"] == 2
    detail = await get_source(hospital.id, source.id, include_noise=True, db=pg_async_session)
    assert len(detail["evidence_notes"]) == 2
