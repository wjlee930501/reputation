import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.essence import HospitalSourceAsset, SourceStatus, SourceType
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.services.essence_engine import compute_source_content_hash
from app.services.source_processing_runs import (
    SOURCE_PROCESSING_METADATA_KEY,
    SOURCE_PROCESSING_OPERATION,
    processing_input_hash,
)
from app.workers import tasks


@pytest.mark.parametrize("source_count", [51, 120])
def test_durable_run_drains_every_snapshotted_source_to_completion(
    pg_engine, monkeypatch, source_count
) -> None:
    hospital_id = uuid.uuid4()
    run_id = uuid.uuid4()
    source_ids = [uuid.uuid4() for _ in range(source_count)]
    with Session(pg_engine) as seed:
        seed.add(Hospital(id=hospital_id, name="drain proof", slug=f"drain-{uuid.uuid4().hex}"))
        seed.add_all(
            HospitalSourceAsset(
                id=source_id,
                hospital_id=hospital_id,
                source_type=SourceType.INTERVIEW,
                title=f"자료 {index}",
                raw_text=f"검증 원문 {index}",
                source_metadata={},
                status=SourceStatus.PENDING,
            )
            for index, source_id in enumerate(source_ids)
        )
        seed.add(
            OperationRun(
                id=run_id,
                hospital_id=hospital_id,
                operation_type=SOURCE_PROCESSING_OPERATION,
                state=OperationRunState.REQUESTED,
                idempotency_key=f"drain-{uuid.uuid4()}",
                total_count=source_count,
                request_payload={
                    "source_ids": [str(source_id) for source_id in source_ids],
                    "cursor": 0,
                    "in_flight": 0,
                    "item_results": {},
                },
            )
        )
        seed.commit()

    dispatched: list[list[str]] = []

    def capture_dispatch(_task_name, *, args, **_kwargs):
        dispatched.append(args)

    async def no_provider_calls(*_args, **_kwargs):
        return []

    monkeypatch.setattr(tasks.celery_app, "send_task", capture_dispatch)
    monkeypatch.setattr(tasks, "_metered_process_source_asset", no_provider_calls)
    monkeypatch.setattr(tasks.auto_review_essence_snapshot, "apply_async", lambda **_kwargs: None)

    try:
        assert tasks._dispatch_next_source_processing_run_item(run_id)
        completed = 0
        while dispatched:
            args = dispatched.pop(0)
            result = tasks.process_source_asset_task.run(*args)
            assert result["status"] == SourceStatus.PROCESSED.value
            completed += 1

        with Session(pg_engine) as verify:
            run = verify.get(OperationRun, run_id)
            assert completed == source_count
            assert run.state == OperationRunState.SUCCEEDED
            assert run.success_count == source_count
            assert run.failure_count == 0
            assert run.skipped_count == 0
            assert len(run.request_payload["item_results"]) == source_count
            assert run.request_payload["in_flight"] == 0
    finally:
        with Session(pg_engine) as cleanup:
            cleanup.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
            hospital = cleanup.get(Hospital, hospital_id)
            if hospital is not None:
                cleanup.delete(hospital)
            cleanup.commit()


def test_transient_block_releases_source_and_defers_same_run_item(pg_engine) -> None:
    hospital_id = uuid.uuid4()
    source_id = uuid.uuid4()
    run_id = uuid.uuid4()
    raw_text = "환자에게 충분히 설명합니다."
    content_hash = compute_source_content_hash("원장 인터뷰", None, raw_text, None)
    source_stub = type(
        "SourceInput",
        (),
        {
            "source_type": SourceType.INTERVIEW,
            "source_metadata": {},
        },
    )()
    input_hash = processing_input_hash(source_stub, content_hash)
    claim_token = "source-attempt"
    dispatch_token = "dispatch-attempt"
    with Session(pg_engine) as seed:
        seed.add(Hospital(id=hospital_id, name="defer proof", slug=f"defer-{uuid.uuid4().hex}"))
        seed.add(
            HospitalSourceAsset(
                id=source_id,
                hospital_id=hospital_id,
                source_type=SourceType.INTERVIEW,
                title="원장 인터뷰",
                raw_text=raw_text,
                content_hash=content_hash,
                status=SourceStatus.PENDING,
                source_metadata={
                    SOURCE_PROCESSING_METADATA_KEY: {
                        "token": claim_token,
                        "input_hash": input_hash,
                        "claimed_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
        )
        seed.add(
            OperationRun(
                id=run_id,
                hospital_id=hospital_id,
                operation_type=SOURCE_PROCESSING_OPERATION,
                state=OperationRunState.QUEUED,
                idempotency_key=f"recovery-{uuid.uuid4()}",
                total_count=1,
                request_payload={
                    "source_ids": [str(source_id)],
                    "cursor": 1,
                    "in_flight": 1,
                    "in_flight_source_id": str(source_id),
                    "in_flight_dispatch_token": dispatch_token,
                    "item_results": {},
                },
            )
        )
        seed.commit()

    try:
        assert tasks._release_source_processing_claim_for_recovery(
            source_id,
            claim_token=claim_token,
            input_hash=input_hash,
            reason="cost blocked",
        )
        assert tasks._defer_source_processing_run_item(
            run_id,
            source_id,
            dispatch_token=dispatch_token,
            reason="COST_BLOCKED",
        )

        with Session(pg_engine) as verify:
            source = verify.get(HospitalSourceAsset, source_id)
            run = verify.get(OperationRun, run_id)
            assert source.status == SourceStatus.PENDING
            assert SOURCE_PROCESSING_METADATA_KEY not in source.source_metadata
            assert run.state == OperationRunState.REQUESTED
            assert run.success_count == 0
            assert run.failure_count == 0
            assert run.skipped_count == 0
            assert run.request_payload["cursor"] == 0
            assert run.request_payload["in_flight"] == 0
            assert run.request_payload["item_results"] == {}
            assert run.request_payload["next_retry_at"]
    finally:
        with Session(pg_engine) as cleanup:
            cleanup.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
            hospital = cleanup.get(Hospital, hospital_id)
            if hospital is not None:
                cleanup.delete(hospital)
            cleanup.commit()


def test_stale_source_result_cannot_overwrite_operator_edit(pg_engine) -> None:
    hospital_id = uuid.uuid4()
    source_id = uuid.uuid4()
    with Session(pg_engine) as seed:
        seed.add(Hospital(id=hospital_id, name="CAS proof", slug=f"cas-{uuid.uuid4().hex}"))
        seed.add(
            HospitalSourceAsset(
                id=source_id,
                hospital_id=hospital_id,
                source_type=SourceType.INTERVIEW,
                title="원장 인터뷰",
                raw_text="기존 원문",
                source_metadata={},
                status=SourceStatus.PENDING,
            )
        )
        seed.commit()

    try:
        claimed, status, input_hash = tasks._claim_source_processing(
            source_id, claim_token="provider-attempt"
        )
        assert claimed is not None and status == "CLAIMED" and input_hash
        with Session(pg_engine) as edit:
            source = edit.get(HospitalSourceAsset, source_id)
            source.raw_text = "운영자가 외부 호출 중 수정한 새 원문"
            source.content_hash = compute_source_content_hash(
                source.title, source.url, source.raw_text, source.operator_note
            )
            edit.commit()

        outcome = tasks._write_source_processing_result(
            source_id,
            claim_token="provider-attempt",
            input_hash=input_hash,
            payloads=[],
        )

        with Session(pg_engine) as verify:
            source = verify.get(HospitalSourceAsset, source_id)
            assert outcome[0] == "SUPERSEDED"
            assert source.raw_text == "운영자가 외부 호출 중 수정한 새 원문"
            assert source.status == SourceStatus.PENDING
    finally:
        with Session(pg_engine) as cleanup:
            hospital = cleanup.get(Hospital, hospital_id)
            if hospital is not None:
                cleanup.delete(hospital)
            cleanup.commit()


def test_live_source_claim_fences_duplicate_redelivery_even_with_same_token(pg_engine) -> None:
    hospital_id = uuid.uuid4()
    source_id = uuid.uuid4()
    with Session(pg_engine) as seed:
        seed.add(Hospital(id=hospital_id, name="lease proof", slug=f"lease-{uuid.uuid4().hex}"))
        seed.add(
            HospitalSourceAsset(
                id=source_id,
                hospital_id=hospital_id,
                source_type=SourceType.INTERVIEW,
                title="원장 인터뷰",
                raw_text="환자에게 충분히 설명합니다.",
                source_metadata={},
                status=SourceStatus.PENDING,
            )
        )
        seed.commit()

    try:
        claimed, status, input_hash = tasks._claim_source_processing(
            source_id, claim_token="same-delivery-token"
        )
        duplicate, duplicate_status, duplicate_hash = tasks._claim_source_processing(
            source_id, claim_token="same-delivery-token"
        )

        assert claimed is not None and status == "CLAIMED"
        assert duplicate is None
        assert duplicate_status == "PROCESSING"
        assert duplicate_hash == input_hash
    finally:
        with Session(pg_engine) as cleanup:
            hospital = cleanup.get(Hospital, hospital_id)
            if hospital is not None:
                cleanup.delete(hospital)
            cleanup.commit()


def _seed_extracted_source(pg_engine, *, status, metadata, with_note=True, processed=True):
    from app.models.essence import HospitalSourceEvidenceNote

    hospital_id = uuid.uuid4()
    source_id = uuid.uuid4()
    title, raw_text = "네이버 블로그 글", "같은 원문 본문입니다. 진료 시간은 평일 9시부터입니다."
    content_hash = compute_source_content_hash(title, None, raw_text, None)
    with Session(pg_engine) as seed:
        seed.add(Hospital(id=hospital_id, name="W2 unchanged", slug=f"w2-{uuid.uuid4().hex}"))
        seed.flush()
        seed.add(
            HospitalSourceAsset(
                id=source_id,
                hospital_id=hospital_id,
                source_type=SourceType.NAVER_BLOG,
                title=title,
                raw_text=raw_text,
                source_metadata=metadata(content_hash),
                content_hash=content_hash,
                status=status,
                processed_at=datetime.now(timezone.utc) if processed else None,
            )
        )
        seed.flush()
        if with_note:
            seed.add(
                HospitalSourceEvidenceNote(
                    hospital_id=hospital_id,
                    source_asset_id=source_id,
                    note_type="KEY_MESSAGE",
                    claim="진료 시간은 평일 9시부터",
                    source_excerpt="진료 시간은 평일 9시부터입니다.",
                    confidence=0.9,
                    note_metadata={},
                )
            )
        seed.commit()
    return hospital_id, source_id


def _cleanup_hospital(pg_engine, hospital_id) -> None:
    with Session(pg_engine) as cleanup:
        hospital = cleanup.get(Hospital, hospital_id)
        if hospital is not None:
            cleanup.delete(hospital)
        cleanup.commit()


@pytest.mark.parametrize(
    ("status", "metadata"),
    [
        # 옛 PROCESSED 행: 추출 뒤 수집 시각만 바뀌었다(입력 hash 불일치, 본문 hash 같음).
        (SourceStatus.PROCESSED, lambda h: {"crawled_at": "2026-10-13T03:00:00+00:00"}),
        # 같은 본문을 다시 수집해 PENDING이 된 행(추출 표지가 같은 본문을 가리킨다).
        (
            SourceStatus.PENDING,
            lambda h: {"crawled_at": "2026-10-13T03:00:00+00:00", "extraction_content_hash": h},
        ),
    ],
)
def test_unchanged_source_content_keeps_notes_without_provider_call(
    pg_engine, status, metadata
) -> None:
    from app.models.essence import HospitalSourceEvidenceNote

    hospital_id, source_id = _seed_extracted_source(pg_engine, status=status, metadata=metadata)
    try:
        claimed, claim_status, input_hash = tasks._claim_source_processing(
            source_id, claim_token="weekly-sync"
        )
        assert claimed is None
        assert claim_status == tasks.SOURCE_EVIDENCE_UNCHANGED
        with Session(pg_engine) as verify:
            source = verify.get(HospitalSourceAsset, source_id)
            assert source.status == SourceStatus.PROCESSED
            assert source.source_metadata["extraction_input_hash"] == input_hash
            assert source.source_metadata["extraction_content_hash"] == source.content_hash
            notes = verify.scalars(
                select(HospitalSourceEvidenceNote).where(
                    HospitalSourceEvidenceNote.source_asset_id == source_id
                )
            ).all()
            assert len(notes) == 1
        # 다시 와도 이미 현재 판이다.
        _claimed, again, _hash = tasks._claim_source_processing(source_id, claim_token="again")
        assert again == SourceStatus.PROCESSED.value
    finally:
        _cleanup_hospital(pg_engine, hospital_id)


@pytest.mark.parametrize(
    ("status", "metadata", "with_note", "processed"),
    [
        # 본문이 바뀌었다(추출 표지가 옛 본문을 가리킨다).
        (SourceStatus.PENDING, lambda h: {"extraction_content_hash": "old"}, True, True),
        # PATCH가 노트를 지우고 처리 시각을 비웠다.
        (SourceStatus.PENDING, lambda h: {"extraction_content_hash": h}, False, False),
        # 처리 판이 올랐다.
        (
            SourceStatus.PROCESSED,
            lambda h: {"extraction_content_hash": h, "extraction_processing_version": 1},
            True,
            True,
        ),
    ],
)
def test_changed_or_cleared_source_is_extracted_again(
    pg_engine, status, metadata, with_note, processed
) -> None:
    hospital_id, source_id = _seed_extracted_source(
        pg_engine, status=status, metadata=metadata, with_note=with_note, processed=processed
    )
    try:
        claimed, claim_status, _input_hash = tasks._claim_source_processing(
            source_id, claim_token="changed"
        )
        assert claimed is not None and claim_status == "CLAIMED"
    finally:
        _cleanup_hospital(pg_engine, hospital_id)
