"""M-01: 원문을 추출할 수 없는 URL 전용 자료가 승인을 영원히 막지 않는다."""

import uuid
from datetime import datetime, timezone

import pytest

from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.essence_engine import (
    build_monthly_essence_summary,
    compute_sources_snapshot_hash,
)
from app.services.essence_readiness import (
    get_current_approved_philosophy_id,
    get_essence_readiness,
    get_essence_readiness_states,
    get_essence_readiness_sync,
    get_public_approved_philosophy_id,
    get_public_approved_philosophy_ids,
    get_public_essence_readiness,
)
from app.services.evidence_noise import compute_evidence_noise_hash


async def _seed(db):
    label = uuid.uuid4().hex[:8]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"URL 자료 {label}",
        slug=f"urlonly-{label}",
        status=HospitalStatus.ACTIVE,
        site_live=False,
    )
    processed = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="원장 인터뷰",
        raw_text="진료 전에 충분히 설명합니다.",
        content_hash=f"{label}-a",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    url_only = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="홈페이지",
        url="https://clinic.example.com",
        raw_text=None,
        content_hash=f"{label}-b",
        status=SourceStatus.PENDING,
    )
    approved = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        source_asset_ids=[str(processed.id)],
        source_snapshot_hash=compute_sources_snapshot_hash([processed]),
        evidence_noise_hash=compute_evidence_noise_hash([]),
        # NOT NULL JSON 필드 — test_essence_approve_grounding.py의 draft 시드와 같은 집합을 채운다
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=[],
        avoid_messages=[],
        treatment_narratives=[],
        local_context={},
        medical_ad_risk_rules=[],
        evidence_map={},
        unsupported_gaps=[],
        conflict_notes=[],
    )
    db.add_all([hospital, processed, url_only, approved])
    await db.commit()
    return hospital, approved, url_only


@pytest.mark.asyncio
async def test_url_only_source_without_text_does_not_block_current(pg_async_session):
    hospital, approved, url_only = await _seed(pg_async_session)
    readiness = await get_essence_readiness(pg_async_session, hospital.id)
    assert readiness.required_source_count == 1
    assert readiness.current is not None and readiness.current.id == approved.id


@pytest.mark.asyncio
async def test_url_only_source_becomes_required_once_it_has_text(pg_async_session):
    hospital, approved, url_only = await _seed(pg_async_session)
    url_only.raw_text = "크롤링으로 채워진 본문"
    await pg_async_session.commit()
    readiness = await get_essence_readiness(pg_async_session, hospital.id)
    assert readiness.required_source_count == 2
    assert readiness.current is not None and readiness.current.id == approved.id
    assert readiness.has_unprocessed_sources is True
    assert readiness.is_stale is True


@pytest.mark.asyncio
@pytest.mark.parametrize("with_approved_fallback", [False, True])
async def test_archived_base_is_never_current_even_with_legacy_flag(
    pg_async_session, with_approved_fallback
):
    """An old worker may archive a base without clearing is_base during rollout."""
    db = pg_async_session
    hospital, archived, _url_only = await _seed(db)
    archived.is_base = True
    archived.status = PhilosophyStatus.ARCHIVED
    fallback = None
    if with_approved_fallback:
        fallback = HospitalContentPhilosophy(
            hospital_id=hospital.id,
            version=2,
            status=PhilosophyStatus.APPROVED,
            is_base=False,
            source_asset_ids=archived.source_asset_ids,
            source_snapshot_hash=archived.source_snapshot_hash,
            evidence_noise_hash=archived.evidence_noise_hash,
        )
        db.add(fallback)
    await db.flush()
    expected_id = fallback.id if fallback is not None else None

    readiness = await get_essence_readiness(db, hospital.id)
    assert readiness.current is fallback
    assert readiness.public_philosophy is fallback
    assert (await db.run_sync(get_essence_readiness_sync, hospital.id)).current is fallback
    assert await get_public_essence_readiness(db, hospital.id) is fallback
    assert await get_current_approved_philosophy_id(db, hospital.id) == expected_id
    assert await get_public_approved_philosophy_id(db, hospital.id) == expected_id
    assert await get_public_approved_philosophy_ids(db, [hospital.id]) == {
        hospital.id: expected_id
    }
    states = await get_essence_readiness_states(db, [hospital.id])
    assert states[hospital.id].current is with_approved_fallback


@pytest.mark.asyncio
async def test_whitespace_only_text_counts_as_no_text(pg_async_session):
    hospital, approved, url_only = await _seed(pg_async_session)
    url_only.raw_text = "   \n\t"
    await pg_async_session.commit()
    readiness = await get_essence_readiness(pg_async_session, hospital.id)
    assert readiness.required_source_count == 1


@pytest.mark.asyncio
async def test_unicode_whitespace_only_text_counts_as_no_text(pg_async_session):
    """워커는 str.strip()으로 빈 본문을 거부한다 — DB 판정이 좁으면 처리 불가 자료가 필수가 된다."""
    hospital, approved, url_only = await _seed(pg_async_session)
    # NBSP·전각 공백·줄 구분자 — 모두 Python의 str.strip()이 깎는 문자다.
    url_only.raw_text = " \u00a0\u3000\u2028\u205f"
    await pg_async_session.commit()
    readiness = await get_essence_readiness(pg_async_session, hospital.id)
    assert readiness.required_source_count == 1
    assert readiness.current is not None and readiness.current.id == approved.id


@pytest.mark.asyncio
async def test_monthly_summary_uses_the_same_required_source_set(pg_async_session):
    """월간 완결성 집계도 실시간 게이트와 같은 분모를 써야 한다 — 아니면 매달 stale로 보고된다."""
    hospital, _approved, _url_only = await _seed(pg_async_session)

    summary = await pg_async_session.run_sync(
        build_monthly_essence_summary,
        hospital,
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 31, tzinfo=timezone.utc),
    )

    assert summary["source_count"] == 1
    assert summary["processed_source_count"] == 1
    assert summary["source_stale"] is False
