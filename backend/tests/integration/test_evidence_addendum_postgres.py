"""Future-only evidence and explicit retraction through real HTTP/PostgreSQL."""

import uuid
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from sqlalchemy import func, select, update

from app.api.admin import essence as essence_api
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.content import (
    ContentItem,
    ContentRevision,
    ContentRevisionApprovalStatus,
    ContentSchedule,
    ContentStatus,
    ContentType,
)
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital

pytestmark = pytest.mark.asyncio


async def _seed_approved_article(db):
    now = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    hospital = Hospital(name="근거 변경 병원", slug=f"evidence-{uuid.uuid4().hex[:10]}")
    db.add(hospital)
    await db.flush()
    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="승인 당시 공식 자료",
        raw_text="승인 당시 확인된 진료 정보",
        content_hash="a" * 64,
        status=SourceStatus.PROCESSED,
        processed_at=now,
        source_metadata={"operator_label": "original"},
    )
    db.add(source)
    await db.flush()
    philosophy = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        is_base=True,
        source_asset_ids=[str(source.id)],
        source_snapshot_hash="b" * 64,
        unsupported_gaps=[],
        approved_at=now,
    )
    schedule = ContentSchedule(
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[1, 3],
        active_from=date(2026, 10, 1),
    )
    db.add_all([philosophy, schedule])
    await db.flush()
    generation_provenance = {"evidence_source_asset_ids": [str(source.id)]}
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        title="승인된 기존 글",
        body="승인된 기존 본문",
        scheduled_date=date(2026, 10, 1),
        status=ContentStatus.PUBLISHED,
        published_at=now,
        published_by="ae@example.com",
        first_published_at=now,
        first_published_by="ae@example.com",
        content_philosophy_id=philosophy.id,
        generation_philosophy_id=philosophy.id,
        essence_status="ALIGNED",
        essence_check_summary={"generation_provenance": generation_provenance},
        references_list=[],
        reference_checks=[],
        content_brief={"schema_version": "test-v1", "source_snapshot": {"hash": "b" * 64}},
        generation_claim_token=uuid.uuid4(),
        generation_claimed_at=now,
    )
    db.add(item)
    await db.flush()
    revision = ContentRevision(
        content_item_id=item.id,
        edition_no=1,
        legacy_content_revision=item.content_revision,
        title=item.title,
        body=item.body,
        references_list=[],
        reference_checks=[],
        source_snapshot=item.content_brief,
        generation_provenance=generation_provenance,
        generation_philosophy_id=philosophy.id,
        generated_at=now,
        source_snapshot_hash="b" * 64,
        source_fingerprint="c" * 64,
        approval_hash="d" * 64,
        approval_status=ContentRevisionApprovalStatus.APPROVED,
        approved_at=now,
        approved_by="ae@example.com",
    )
    db.add(revision)
    await db.flush()
    item.active_revision_id = revision.id
    await db.flush()
    return hospital, source, philosophy, item, revision


async def _client(db):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    return (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test"),
        previous_limiter,
    )


async def test_future_only_addendum_preserves_active_revision_and_original_source(
    pg_async_session,
    monkeypatch,
) -> None:
    hospital, source, _philosophy, item, revision = await _seed_approved_article(
        pg_async_session
    )
    original = {
        "source_hash": source.content_hash,
        "source_text": source.raw_text,
        "active_revision_id": item.active_revision_id,
        "first_published_at": item.first_published_at,
        "published_at": item.published_at,
        "content_revision": item.content_revision,
        "approval_hash": revision.approval_hash,
    }

    async def no_processing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(essence_api, "_start_source_processing_best_effort", no_processing)
    monkeypatch.setattr(essence_api, "_enqueue_essence_review_best_effort", lambda *_: None)
    client, previous_limiter = await _client(pg_async_session)
    try:
        async with client:
            response = await client.post(
                f"/api/v1/admin/hospitals/{hospital.id}/essence/sources/{source.id}/addenda",
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor-System": "task8-qa",
                },
                json={
                    "raw_text": "향후 후보부터 참고할 추가 정보",
                    "source_metadata": {
                        "operator_label": "future",
                        "_source_version": {
                            "mode": "CORRECTION",
                            "root_source_id": str(uuid.uuid4()),
                            "version": 999,
                        },
                    },
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter

    assert response.status_code == 201, response.text
    payload = response.json()
    lineage = payload["source_metadata"]["_source_version"]
    assert lineage == {
        "mode": "FUTURE_ONLY_ADDENDUM",
        "root_source_id": str(source.id),
        "previous_source_id": str(source.id),
        "version": 2,
    }
    await pg_async_session.refresh(source)
    await pg_async_session.refresh(item)
    await pg_async_session.refresh(revision)
    assert source.content_hash == original["source_hash"]
    assert source.raw_text == original["source_text"]
    assert item.active_revision_id == original["active_revision_id"]
    assert item.first_published_at == original["first_published_at"]
    assert item.published_at == original["published_at"]
    assert item.content_revision == original["content_revision"]
    assert revision.approval_hash == original["approval_hash"]
    assert await pg_async_session.scalar(
        select(func.count()).select_from(ContentRevision).where(
            ContentRevision.content_item_id == item.id
        )
    ) == 1


async def test_explicit_retraction_revokes_pointer_and_fences_stale_writeback(
    pg_async_session,
    monkeypatch,
) -> None:
    hospital, source, philosophy, item, revision = await _seed_approved_article(
        pg_async_session
    )
    old_revision = item.content_revision
    old_claim = item.generation_claim_token
    first_published_at = item.first_published_at
    approval_hash = revision.approval_hash
    monkeypatch.setattr(essence_api, "_enqueue_essence_review_best_effort", lambda *_: None)
    client, previous_limiter = await _client(pg_async_session)
    try:
        async with client:
            response = await client.post(
                f"/api/v1/admin/hospitals/{hospital.id}/essence/sources/{source.id}/exclude",
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor-System": "task8-qa",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter

    assert response.status_code == 200, response.text
    await pg_async_session.refresh(item)
    await pg_async_session.refresh(philosophy)
    await pg_async_session.refresh(revision)
    assert item.status == ContentStatus.REJECTED
    assert item.active_revision_id is None
    assert item.first_published_at == first_published_at
    assert item.content_revision == old_revision + 1
    assert item.generation_claim_token is None
    marker = item.essence_check_summary["authority_change"]
    assert marker["dependency_certainty"] == "EXACT_INPUT"
    assert marker["semantic_claim_dependency"] is False
    assert any(
        gap.get("field") == "authority_change_required"
        and str(source.id) in gap.get("source_ids", [])
        for gap in philosophy.unsupported_gaps
    )
    stale_write = await pg_async_session.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item.id,
            ContentItem.content_revision == old_revision,
            ContentItem.generation_claim_token == old_claim,
        )
        .values(body="stale provider output")
    )
    assert stale_write.rowcount == 0
    assert revision.approval_hash == approval_hash
    assert revision.body == "승인된 기존 본문"


async def test_source_patch_is_conservative_correction_and_revokes_active_pointer(
    pg_async_session,
    monkeypatch,
) -> None:
    hospital, source, _philosophy, item, revision = await _seed_approved_article(
        pg_async_session
    )
    old_revision = item.content_revision
    old_claim = item.generation_claim_token
    immutable_body = revision.body

    async def no_processing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(essence_api, "_start_source_processing_best_effort", no_processing)
    client, previous_limiter = await _client(pg_async_session)
    try:
        async with client:
            response = await client.patch(
                f"/api/v1/admin/hospitals/{hospital.id}/essence/sources/{source.id}",
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor-System": "task8-qa",
                },
                json={"raw_text": "기존 사실이 잘못되어 바로잡은 자료"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter

    assert response.status_code == 200, response.text
    await pg_async_session.refresh(item)
    await pg_async_session.refresh(revision)
    assert item.status == ContentStatus.REJECTED
    assert item.active_revision_id is None
    assert item.content_revision == old_revision + 1
    assert item.essence_check_summary["authority_change"]["reason"] == "SOURCE_CORRECTED"
    stale_write = await pg_async_session.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item.id,
            ContentItem.content_revision == old_revision,
            ContentItem.generation_claim_token == old_claim,
        )
        .values(body="stale correction result")
    )
    assert stale_write.rowcount == 0
    assert revision.body == immutable_body
