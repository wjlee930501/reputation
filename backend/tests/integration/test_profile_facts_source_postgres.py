"""병원 정보(자동) 자료(PROFILE_FACTS, Phase 4 P4-11)를 실제 PostgreSQL에서 확인한다.

병원 행의 서술 값이 결정적 근거 노트가 되고, 그대로인 줄의 노트 id는 동기화에도 유지되며(판·글의
근거가 끊기지 않는다), Admin 자료 API에서는 읽기 전용이다.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin import essence as essence_api
from app.models.essence import (
    EvidenceNoteType,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.essence_sources import required_text_source_predicate
from app.services.profile_facts_source import (
    PROFILE_FACTS_ACTOR,
    existing_profile_facts_source,
    sync_profile_facts_source,
)


@pytest.fixture
def pg_session(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _hospital(**fields) -> Hospital:
    base = dict(
        id=uuid.uuid4(),
        name="병원 정보 자료 병원",
        slug=f"profile-facts-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ONBOARDING,
        director_name="홍길동",
        director_philosophy="환자분이 이해하실 때까지 설명합니다.",
        treatments=[{"name": "도수치료", "description": "손으로 근육을 풉니다"}],
        address="서울시 강남구 테헤란로 1",
        phone="02-1234-5678",
        profile_field_provenance={"director_name": {"origin": "OPERATOR"}},
    )
    base.update(fields)
    return Hospital(**base)


def _notes(db: Session, source_id) -> dict[str, HospitalSourceEvidenceNote]:
    rows = db.scalars(
        select(HospitalSourceEvidenceNote).where(
            HospitalSourceEvidenceNote.source_asset_id == source_id
        )
    ).all()
    return {row.source_excerpt: row for row in rows}


def test_sync_creates_a_processed_required_source_with_one_note_per_line(pg_session) -> None:
    hospital = _hospital()
    pg_session.add(hospital)
    pg_session.flush()

    result = sync_profile_facts_source(pg_session, hospital)
    pg_session.flush()

    assert result.status == "CREATED" and result.changed
    source = pg_session.get(HospitalSourceAsset, result.source_id)
    assert source.source_type == SourceType.INTERNAL_NOTE
    assert source.created_by == PROFILE_FACTS_ACTOR
    assert source.source_metadata["system_kind"] == "PROFILE_FACTS"
    assert source.status == SourceStatus.PROCESSED and source.processed_at is not None
    assert "테헤란로" not in source.raw_text and "02-1234" not in source.raw_text
    required = pg_session.scalars(
        select(HospitalSourceAsset.id).where(
            HospitalSourceAsset.hospital_id == hospital.id, required_text_source_predicate()
        )
    ).all()
    assert required == [source.id]
    notes = _notes(pg_session, source.id)
    assert set(notes) == set(source.raw_text.splitlines())
    assert notes["[마케터 입력] 원장: 홍길동"].note_type == EvidenceNoteType.KEY_MESSAGE
    for excerpt, note in notes.items():
        assert source.raw_text[note.excerpt_start : note.excerpt_end] == excerpt

    assert sync_profile_facts_source(pg_session, hospital).status == "UNCHANGED"


def test_resync_keeps_note_ids_of_unchanged_lines(pg_session) -> None:
    hospital = _hospital()
    pg_session.add(hospital)
    pg_session.flush()
    first = sync_profile_facts_source(pg_session, hospital)
    pg_session.flush()
    before = {excerpt: note.id for excerpt, note in _notes(pg_session, first.source_id).items()}

    hospital.director_philosophy = "검사 결과를 화면으로 함께 보며 설명합니다."
    second = sync_profile_facts_source(pg_session, hospital)
    pg_session.flush()

    assert second.status == "UPDATED"
    assert (second.notes_added, second.notes_removed) == (1, 1)
    after = {excerpt: note.id for excerpt, note in _notes(pg_session, first.source_id).items()}
    kept = "[마케터 입력] 원장: 홍길동"
    assert after[kept] == before[kept]
    assert "[병원 정보] 진료 철학: 검사 결과를 화면으로 함께 보며 설명합니다." in after


def test_clearing_every_narrative_value_takes_the_source_out_of_the_required_set(pg_session) -> None:
    hospital = _hospital()
    pg_session.add(hospital)
    pg_session.flush()
    created = sync_profile_facts_source(pg_session, hospital)
    pg_session.flush()

    hospital.director_name = None
    hospital.director_philosophy = None
    hospital.treatments = []
    cleared = sync_profile_facts_source(pg_session, hospital)
    pg_session.flush()

    assert cleared.status == "CLEARED"
    source = pg_session.get(HospitalSourceAsset, created.source_id)
    assert source.raw_text is None and source.status == SourceStatus.PENDING
    assert _notes(pg_session, source.id) == {}
    assert existing_profile_facts_source(pg_session, hospital.id).id == source.id


@pytest.mark.asyncio
async def test_profile_facts_source_is_read_only_in_the_source_api(pg_async_session) -> None:
    hospital = _hospital()
    pg_async_session.add(hospital)
    await pg_async_session.flush()
    result = await pg_async_session.run_sync(
        lambda session: sync_profile_facts_source(session, session.get(Hospital, hospital.id))
    )
    await pg_async_session.commit()

    for call in (
        essence_api.exclude_source(hospital.id, result.source_id, db=pg_async_session),
        essence_api.process_source(hospital.id, result.source_id, db=pg_async_session),
        essence_api.patch_source(
            hospital.id,
            result.source_id,
            essence_api.SourceAssetPatch(raw_text="사람이 고친 본문"),
            db=pg_async_session,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "PROFILE_FACTS_READ_ONLY"
        await pg_async_session.rollback()

    source = await pg_async_session.get(HospitalSourceAsset, result.source_id)
    assert source.status == SourceStatus.PROCESSED
    display = essence_api._serialize_source_display(source)
    assert display["read_only"] is True
    assert display["source_type_label"] == "병원 정보(자동)"
