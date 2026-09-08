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
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.essence_readiness import get_essence_readiness
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
    assert readiness.current is None


@pytest.mark.asyncio
async def test_whitespace_only_text_counts_as_no_text(pg_async_session):
    hospital, approved, url_only = await _seed(pg_async_session)
    url_only.raw_text = "   \n\t"
    await pg_async_session.commit()
    readiness = await get_essence_readiness(pg_async_session, hospital.id)
    assert readiness.required_source_count == 1
