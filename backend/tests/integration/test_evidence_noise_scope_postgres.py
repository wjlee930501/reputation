"""노이즈 hash의 자료 경계는 readiness의 자료 경계와 같아야 한다.

readiness는 필수 텍스트 자료(제외되지 않은 비사진 자료)만 snapshot에 넣는다. 노이즈 hash가
그보다 넓은 집합을 보면, readiness가 아예 보지 않는 자료의 노트를 토글하는 것만으로 승인이
stale이 된다 — 운영자에게는 아무것도 바뀌지 않은 것처럼 보이는데 생성이 멈춘다.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.models.essence import (
    PHOTO_SOURCE_TYPES,
    EvidenceNoteType,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.evidence_noise import (
    compute_evidence_noise_hash,
    load_evidence_noise_hash,
)


def _hospital(label: str) -> Hospital:
    return Hospital(
        id=uuid.uuid4(),
        name=f"노이즈경계 병원 {label}",
        slug=f"noise-scope-{label}",
        status=HospitalStatus.ACTIVE,
        site_live=False,
    )


def _source(
    hospital_id: uuid.UUID,
    label: str,
    *,
    source_type: SourceType,
    status: SourceStatus,
) -> HospitalSourceAsset:
    return HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        source_type=source_type,
        title=f"{source_type.value} 자료",
        raw_text="진료 전에 충분히 설명하고 환자마다 다른 선택지를 안내합니다.",
        content_hash=f"{label}-{source_type.value}-hash",
        status=status,
        processed_at=datetime.now(timezone.utc),
        file_url=(
            "https://example.test/photo.jpg" if source_type in PHOTO_SOURCE_TYPES else None
        ),
    )


def _noise_note(source: HospitalSourceAsset) -> HospitalSourceEvidenceNote:
    return HospitalSourceEvidenceNote(
        id=uuid.uuid4(),
        hospital_id=source.hospital_id,
        source_asset_id=source.id,
        note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY,
        claim="충분한 설명을 중시한다.",
        source_excerpt="진료 전에 충분히 설명하고 환자마다 다른 선택지를 안내합니다.",
        confidence=0.95,
        note_metadata={"is_noise": True},
    )


async def _seed(db):
    label = uuid.uuid4().hex[:8]
    hospital = _hospital(label)
    other_hospital = _hospital(f"{label}-other")

    required = _source(
        hospital.id, label, source_type=SourceType.INTERVIEW, status=SourceStatus.PROCESSED
    )
    excluded = _source(
        hospital.id, label, source_type=SourceType.HOMEPAGE, status=SourceStatus.EXCLUDED
    )
    photo = _source(
        hospital.id, label, source_type=SourceType.PHOTO_DOCTOR, status=SourceStatus.PROCESSED
    )
    other_source = _source(
        other_hospital.id,
        f"{label}-other",
        source_type=SourceType.INTERVIEW,
        status=SourceStatus.PROCESSED,
    )

    required_note = _noise_note(required)
    excluded_note = _noise_note(excluded)
    photo_note = _noise_note(photo)
    other_note = _noise_note(other_source)

    db.add_all(
        [
            hospital,
            other_hospital,
            required,
            excluded,
            photo,
            other_source,
            required_note,
            excluded_note,
            photo_note,
            other_note,
        ]
    )
    await db.commit()
    return hospital, required_note, excluded_note


@pytest.mark.asyncio
async def test_only_notes_of_required_text_sources_enter_the_hash(pg_async_session):
    hospital, required_note, _excluded_note = await _seed(pg_async_session)

    assert await load_evidence_noise_hash(
        pg_async_session, hospital.id
    ) == compute_evidence_noise_hash([required_note.id])


@pytest.mark.asyncio
async def test_toggling_noise_outside_the_required_set_does_not_move_the_hash(pg_async_session):
    hospital, required_note, excluded_note = await _seed(pg_async_session)
    before = await load_evidence_noise_hash(pg_async_session, hospital.id)

    excluded_note.note_metadata = {"is_noise": False}
    await pg_async_session.commit()
    await pg_async_session.refresh(excluded_note)

    assert await load_evidence_noise_hash(pg_async_session, hospital.id) == before

    required_note.note_metadata = {"is_noise": False}
    await pg_async_session.commit()
    await pg_async_session.refresh(required_note)

    assert await load_evidence_noise_hash(pg_async_session, hospital.id) != before
    assert await load_evidence_noise_hash(
        pg_async_session, hospital.id
    ) == compute_evidence_noise_hash([])
