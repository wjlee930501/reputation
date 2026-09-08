import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.api.admin import essence as essence_api
from app.models.essence import (
    EvidenceNoteType,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.schemas.essence import SourceAssetPatch
from app.services import essence_sources as essence_sources_service
from app.services.essence_engine import compute_source_content_hash
from app.services.source_processing_runs import (
    SOURCE_PROCESSING_METADATA_KEY,
    SOURCE_PROCESSING_OPERATION,
    create_or_get_source_processing_run,
    processing_input_hash,
    source_run_key,
)

pytestmark = pytest.mark.asyncio


async def _hospital(db) -> Hospital:
    hospital = Hospital(name="자료 처리 검증 병원", slug=f"ingestion-{uuid.uuid4().hex}")
    db.add(hospital)
    await db.flush()
    return hospital


async def test_normalized_noop_patch_preserves_processed_notes_and_private_fence(
    pg_async_session, monkeypatch
) -> None:
    db = pg_async_session
    hospital = await _hospital(db)
    processed_at = datetime(2026, 9, 7, tzinfo=timezone.utc)
    content_hash = compute_source_content_hash(
        "원장 철학", "https://example.com/source", "환자에게 충분히 설명합니다.", None
    )
    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="원장 철학",
        url="https://example.com/source",
        raw_text="환자에게 충분히 설명합니다.",
        operator_note=None,
        content_hash=content_hash,
        status=SourceStatus.PROCESSED,
        processed_at=processed_at,
        source_metadata={"channel": "homepage"},
    )
    db.add(source)
    await db.flush()
    input_hash = processing_input_hash(source, content_hash)
    source.source_metadata = {
        "channel": "homepage",
        "extraction_input_hash": input_hash,
        "extraction_coverage": {"complete": True},
        SOURCE_PROCESSING_METADATA_KEY: {
            "token": "server-only",
            "input_hash": input_hash,
        },
    }
    note = HospitalSourceEvidenceNote(
        hospital_id=hospital.id,
        source_asset_id=source.id,
        note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY,
        claim="충분히 설명",
        source_excerpt="충분히 설명",
        excerpt_start=5,
        excerpt_end=12,
        confidence=1.0,
        note_metadata={},
    )
    db.add(note)
    await db.flush()
    await db.refresh(source)

    async def unexpected_dispatch(*_args, **_kwargs):
        raise AssertionError("normalized no-op must not enqueue source extraction")

    monkeypatch.setattr(
        essence_api, "_start_source_processing_best_effort", unexpected_dispatch
    )
    response = await essence_api.patch_source(
        hospital.id,
        source.id,
        SourceAssetPatch(
            url="  https://example.com/source  ",
            raw_text="환자에게 충분히 설명합니다.",
            operator_note="   ",
            source_metadata={"channel": "homepage"},
        ),
        db,
    )

    await db.refresh(source)
    note_count = await db.scalar(
        select(func.count())
        .select_from(HospitalSourceEvidenceNote)
        .where(HospitalSourceEvidenceNote.source_asset_id == source.id)
    )
    assert source.status == SourceStatus.PROCESSED
    assert source.processed_at == processed_at
    assert source.content_hash == content_hash
    assert note_count == 1
    assert SOURCE_PROCESSING_METADATA_KEY in source.source_metadata
    assert SOURCE_PROCESSING_METADATA_KEY not in response["source_metadata"]
    assert "extraction_input_hash" not in response["source_metadata"]
    assert response["source_metadata"]["extraction_coverage"] == {"complete": True}


@pytest.mark.parametrize("source_count", [51, 120])
async def test_process_pending_snapshots_every_item_in_one_bounded_run(
    pg_async_session, monkeypatch, source_count
) -> None:
    db = pg_async_session
    hospital = await _hospital(db)
    sources = [
        HospitalSourceAsset(
            hospital_id=hospital.id,
            source_type=SourceType.HOMEPAGE,
            title=f"자료 {index}",
            raw_text=f"서로 다른 원문 {index}",
            source_metadata={},
            status=SourceStatus.PENDING,
        )
        for index in range(source_count)
    ]
    db.add_all(sources)
    await db.flush()

    async def keep_snapshot_undispatched(_db, _run_id):
        return False

    monkeypatch.setattr(
        essence_sources_service,
        "dispatch_source_processing_run_best_effort",
        keep_snapshot_undispatched,
    )
    result = await essence_api.process_pending_sources(
        hospital.id,
        # This deprecated client limit must not truncate the server-owned snapshot.
        limit=1,
        db=db,
    )

    run = await db.get(OperationRun, uuid.UUID(result.run_id))
    latest = await essence_api.latest_source_processing_run(hospital.id, db)
    fetched = await essence_api.get_source_processing_run(hospital.id, run.id, db)
    assert result.total_count == source_count
    assert set(result.source_ids) == {str(source.id) for source in sources}
    assert run.request_payload["source_ids"] == result.source_ids
    assert run.request_payload["cursor"] == 0
    assert run.request_payload["in_flight"] == 0
    assert latest is not None and latest.run_id == result.run_id
    assert fetched.run_id == result.run_id
    assert fetched.total_count == source_count


async def test_terminal_source_run_gets_new_attempt_while_active_attempt_is_reused(
    pg_async_session,
) -> None:
    db = pg_async_session
    hospital = await _hospital(db)
    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="재시도 자료",
        raw_text="일시 장애 이후 다시 처리해야 하는 원문",
        source_metadata={},
        status=SourceStatus.PENDING,
    )
    db.add(source)
    await db.flush()
    content_hash = compute_source_content_hash(
        source.title, source.url, source.raw_text, source.operator_note
    )
    identity = f"{source.id}:{processing_input_hash(source, content_hash)}"
    base_key = source_run_key([identity])
    terminal = OperationRun(
        hospital_id=hospital.id,
        operation_type=SOURCE_PROCESSING_OPERATION,
        state=OperationRunState.PARTIAL,
        idempotency_key=base_key,
        total_count=1,
        failure_count=1,
        request_payload={"source_ids": [str(source.id)]},
    )
    db.add(terminal)
    await db.commit()

    retry, created = await create_or_get_source_processing_run(
        db,
        hospital_id=hospital.id,
        source_ids=[source.id],
        source_identities=[identity],
    )
    same_retry, created_again = await create_or_get_source_processing_run(
        db,
        hospital_id=hospital.id,
        source_ids=[source.id],
        source_identities=[identity],
    )

    assert created is True
    assert retry.id != terminal.id
    assert retry.idempotency_key.startswith(f"{base_key}:retry:")
    assert retry.state == OperationRunState.REQUESTED
    assert created_again is False
    assert same_retry.id == retry.id


async def test_reinclude_pending_text_source_creates_durable_processing_run(
    pg_async_session, monkeypatch
) -> None:
    db = pg_async_session
    hospital = await _hospital(db)
    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="재포함 자료",
        raw_text="다시 처리할 원문입니다.",
        source_metadata={},
        status=SourceStatus.EXCLUDED,
    )
    db.add(source)
    await db.commit()

    async def keep_snapshot_undispatched(_db, _run_id):
        return False

    monkeypatch.setattr(
        essence_sources_service,
        "dispatch_source_processing_run_best_effort",
        keep_snapshot_undispatched,
    )
    monkeypatch.setattr(essence_api, "_enqueue_essence_review_best_effort", lambda *_args: None)

    response = await essence_api.reinclude_source(hospital.id, source.id, db)
    run = await db.scalar(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital.id,
            OperationRun.operation_type == SOURCE_PROCESSING_OPERATION,
        )
    )

    assert response["status"] == SourceStatus.PENDING.value
    assert run is not None
    assert run.request_payload["source_ids"] == [str(source.id)]
