"""C-5 — 제외한 자료의 근거 노트는 집계·상세·위키 어디에도 남지 않고, 제외는 되돌릴 수 있다.

제외는 상태만 바꾸고 노트 행은 남긴다. 노트를 세는 쿼리가 자료 상태를 보지 않으면
제외한 유튜브 채널 홈의 조각들이 근거 합계와 Wiki에 계속 나타난다. 반대로 노트를
지워 버리면 제외 해제가 재처리(LLM 비용) 없이는 불가능하다. 두 요구가 동시에
만족되는지 실제 SQL로 확인한다.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.admin import essence as essence_api
from app.models.audit import AdminAuditLog
from app.models.essence import (
    EvidenceNoteType,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus


@pytest.fixture
def seeded(pg_async_session):
    """제외 대상 유튜브 자료와 그대로 남을 홈페이지 자료 각각 노트를 붙여 심는다."""
    hospital = Hospital(
        id=uuid.uuid4(),
        name="장편한외과의원",
        slug=f"exclusion-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        # 공개 사이트가 없으면 revalidate 설정을 요구하지 않는다.
        site_live=False,
    )
    youtube = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.YOUTUBE,
        title="유튜브 채널 홈",
        raw_text="구독 재생목록 로그인 채널 홈 탐색 메뉴",
        content_hash=f"youtube-{uuid.uuid4().hex[:8]}",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    homepage = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="병원 공식 홈페이지",
        raw_text="진료 전에 충분히 설명하고 환자마다 다른 선택지를 안내합니다.",
        content_hash=f"homepage-{uuid.uuid4().hex[:8]}",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    notes = [
        HospitalSourceEvidenceNote(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            source_asset_id=source.id,
            note_type=EvidenceNoteType.KEY_MESSAGE,
            claim=claim,
            source_excerpt=source.raw_text,
            confidence=0.8,
            note_metadata={},
        )
        for source, claim in (
            (youtube, "구독 버튼 안내"),
            (youtube, "재생목록 안내"),
            (homepage, "충분한 설명을 중시한다."),
        )
    ]
    return hospital, youtube, homepage, notes


async def _seed(session, seeded):
    hospital, youtube, homepage, notes = seeded
    session.add_all([hospital, youtube, homepage, *notes])
    await session.flush()
    return hospital, youtube, homepage


def _by_id(payloads, source_id):
    return next(item for item in payloads if item["id"] == str(source_id))


async def test_excluded_source_notes_leave_the_totals_and_the_detail(
    pg_async_session, seeded, monkeypatch
):
    monkeypatch.setattr(essence_api, "_enqueue_essence_review_best_effort", lambda *_: None)
    hospital, youtube, homepage = await _seed(pg_async_session, seeded)

    before = await essence_api.list_sources(hospital.id, status_filter=None, source_type=None, db=pg_async_session)
    assert _by_id(before, youtube.id)["evidence_note_count"] == 2
    assert _by_id(before, homepage.id)["evidence_note_count"] == 1

    excluded = await essence_api.exclude_source(hospital.id, youtube.id, db=pg_async_session)
    assert excluded["status"] == SourceStatus.EXCLUDED
    # 제외 응답 자체가 조각을 되돌려주면 화면은 다시 그것을 보여준다.
    assert excluded["evidence_note_count"] == 0
    assert excluded["evidence_notes"] == []

    after = await essence_api.list_sources(hospital.id, status_filter=None, source_type=None, db=pg_async_session)
    assert _by_id(after, youtube.id)["evidence_note_count"] == 0
    # 제외는 다른 자료의 근거를 건드리지 않는다.
    assert _by_id(after, homepage.id)["evidence_note_count"] == 1

    detail = await essence_api.get_source(hospital.id, youtube.id, db=pg_async_session)
    assert detail["evidence_notes"] == []
    assert detail["evidence_note_count"] == 0


async def test_excluding_a_source_keeps_its_notes_on_disk_for_re_inclusion(
    pg_async_session, seeded, monkeypatch
):
    monkeypatch.setattr(essence_api, "_enqueue_essence_review_best_effort", lambda *_: None)
    hospital, youtube, _ = await _seed(pg_async_session, seeded)

    await essence_api.exclude_source(hospital.id, youtube.id, db=pg_async_session)

    # 행은 그대로 있어야 한다 — 재처리 없이 되돌리는 유일한 방법이다.
    surviving = await pg_async_session.execute(
        select(func.count())
        .select_from(HospitalSourceEvidenceNote)
        .where(HospitalSourceEvidenceNote.source_asset_id == youtube.id)
    )
    assert surviving.scalar_one() == 2


async def test_re_including_a_processed_source_restores_its_notes_without_reprocessing(
    pg_async_session, seeded, monkeypatch
):
    monkeypatch.setattr(essence_api, "_enqueue_essence_review_best_effort", lambda *_: None)
    hospital, youtube, _ = await _seed(pg_async_session, seeded)
    await essence_api.exclude_source(hospital.id, youtube.id, db=pg_async_session)

    restored = await essence_api.reinclude_source(hospital.id, youtube.id, db=pg_async_session)

    assert restored["status"] == SourceStatus.PROCESSED
    assert restored["evidence_note_count"] == 2
    listed = await essence_api.list_sources(hospital.id, status_filter=None, source_type=None, db=pg_async_session)
    assert _by_id(listed, youtube.id)["evidence_note_count"] == 2


async def test_re_including_a_never_processed_source_returns_it_to_pending(
    pg_async_session, seeded, monkeypatch
):
    monkeypatch.setattr(essence_api, "_enqueue_essence_review_best_effort", lambda *_: None)
    hospital = seeded[0]
    unprocessed = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="원장 인터뷰 초안",
        raw_text="아직 근거 추출을 돌리지 않은 자료입니다.",
        content_hash=f"pending-{uuid.uuid4().hex[:8]}",
        status=SourceStatus.PENDING,
    )
    pg_async_session.add_all([hospital, unprocessed])
    await pg_async_session.flush()
    await essence_api.exclude_source(hospital.id, unprocessed.id, db=pg_async_session)

    restored = await essence_api.reinclude_source(hospital.id, unprocessed.id, db=pg_async_session)

    assert restored["status"] == SourceStatus.PENDING
    assert restored["evidence_note_count"] == 0


async def test_re_including_a_public_photo_does_not_republish_it(
    pg_async_session, monkeypatch
):
    """사진 공개는 권리 근거를 확인한 별도 결정이다. 제외 해제가 조용히 되살리면 안 된다."""
    monkeypatch.setattr(essence_api, "_enqueue_essence_review_best_effort", lambda *_: None)
    hospital = Hospital(
        id=uuid.uuid4(),
        name="장편한외과의원",
        slug=f"exclusion-photo-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        site_live=False,
    )
    photo = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.PHOTO_CLINIC_EXTERIOR,
        title="병원 외관",
        file_url="local://photos/exterior.jpg",
        content_hash=f"photo-{uuid.uuid4().hex[:8]}",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
        is_public=True,
        photo_source_owner="장편한외과의원",
        photo_rights_basis="OWNER_CONSENT",
        photo_evidence_reference="계약서 2026-01",
        photo_verified_by="AE QA",
        photo_verified_at=datetime.now(timezone.utc),
    )
    pg_async_session.add_all([hospital, photo])
    await pg_async_session.flush()

    await essence_api.exclude_source(hospital.id, photo.id, db=pg_async_session)
    restored = await essence_api.reinclude_source(hospital.id, photo.id, db=pg_async_session)

    assert restored["is_public"] is False


async def test_re_including_a_source_that_is_not_excluded_is_rejected(
    pg_async_session, seeded, monkeypatch
):
    monkeypatch.setattr(essence_api, "_enqueue_essence_review_best_effort", lambda *_: None)
    hospital, youtube, _ = await _seed(pg_async_session, seeded)

    with pytest.raises(HTTPException) as exc:
        await essence_api.reinclude_source(hospital.id, youtube.id, db=pg_async_session)

    assert exc.value.status_code == 400
    assert "제외 상태가 아닌" in str(exc.value.detail)


async def test_re_inclusion_is_recorded_in_the_audit_trail(
    pg_async_session, seeded, monkeypatch
):
    monkeypatch.setattr(essence_api, "_enqueue_essence_review_best_effort", lambda *_: None)
    hospital, youtube, _ = await _seed(pg_async_session, seeded)
    await essence_api.exclude_source(hospital.id, youtube.id, db=pg_async_session)
    await essence_api.reinclude_source(hospital.id, youtube.id, db=pg_async_session)

    result = await pg_async_session.execute(
        select(AdminAuditLog).where(
            AdminAuditLog.hospital_id == hospital.id,
            AdminAuditLog.action == "reinclude_source_asset",
        )
    )
    entry = result.scalars().one()
    assert entry.detail["restored_note_count"] == 2
    assert entry.detail["to_status"] == str(SourceStatus.PROCESSED)
