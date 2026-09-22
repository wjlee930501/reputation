"""Real PostgreSQL ownership proofs; require a disposable test DSN explicitly."""
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from test_geo_autonomy_hardening import NOW, make_execution

from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.workers import operation_run_signals
from app.workers.generation_execution_claim import begin_generation_execution
from app.workers.nightly_generation_batch import (
    release_unfinished_claims,
    write_back_generated_content,
)

URL = os.getenv("REDELIVERY_TEST_SYNC_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Explicit disposable PostgreSQL URL required")


@pytest.fixture
def live(monkeypatch):
    parsed = make_url(URL)
    assert parsed.host == "127.0.0.1" and parsed.database == "reputation_redelivery_test"
    assert parsed.port and 49152 <= parsed.port <= 65535
    engine = create_engine(URL, pool_size=5, connect_args={"options": "-c lock_timeout=5000 -c statement_timeout=15000"})
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", sessions)
    with sessions() as db:
        item, reservation, context, run = make_execution(db)
        hospital_id, item_id, run_id = item.hospital_id, item.id, run.id
    try:
        yield sessions, item_id, reservation, context, run_id
    finally:
        with sessions() as db:
            db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
            db.execute(delete(Hospital).where(Hospital.id == hospital_id))
            db.commit()
        engine.dispose()


def next_claim(context):
    version = operation_run_signals._claim_safely(context.run_id, context.worker_id, NOW,
                                                redelivered=True)
    assert version is not None and version > context.version
    return replace(context, version=version)


def test_newer_claim_recovers_and_fences_old_writer_and_cleanup(live):
    sessions, item_id, reservation, context, _ = live
    with sessions() as db:
        item, old_token = begin_generation_execution(db, item_id, reservation, context, now=NOW)
        old_time, revision = item.generation_claimed_at, item.content_revision
    newer = next_claim(context)
    with sessions() as db:
        item, new_token = begin_generation_execution(db, item_id, reservation, newer, now=NOW)
        assert new_token != old_token
    with sessions() as stale:
        assert write_back_generated_content(stale, item_id=item_id, expected_revision=revision,
            expected_claim_token=old_token, values={"body": "stale result"}) == 0
        assert release_unfinished_claims(stale, [item_id], expected_claimed_at=old_time,
                                        expected_claim_token=old_token) == 0
        stale.commit()
    with sessions() as current:
        assert write_back_generated_content(current, item_id=item_id, expected_revision=revision,
            expected_claim_token=new_token, values={"body": "current result"}) == 1
        current.commit()
    with sessions() as check:
        assert check.get(ContentItem, item_id).body == "current result"
        saved = check.get(OperationRun, context.run_id).request_payload["generation_execution"]
        assert saved["run_version"] == newer.version
        assert saved["execution_token"] == str(new_token)


def test_two_simultaneous_executions_have_exactly_one_winner(live):
    sessions, item_id, reservation, context, _ = live
    barrier = threading.Barrier(2)

    def attempt():
        with sessions() as db:
            barrier.wait(timeout=5)
            result = begin_generation_execution(db, item_id, reservation, context, now=NOW)
            return None if result is None else result[1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sum(value is not None for value in results) == 1
    with sessions() as db:
        assert db.get(ContentItem, item_id).generation_claim_token in results


@pytest.mark.parametrize("mutation", ["cancelled", "published", "reclaimed", "paused", "edited"])
def test_redelivery_does_not_undo_newer_human_or_scheduler_action(live, mutation):
    sessions, item_id, reservation, context, _ = live
    with sessions() as db:
        assert begin_generation_execution(db, item_id, reservation, context, now=NOW)
    with sessions() as db:
        item = db.get(ContentItem, item_id)
        if mutation == "cancelled":
            item.status = ContentStatus.CANCELLED
        elif mutation == "published":
            item.status = ContentStatus.PUBLISHED
        elif mutation == "reclaimed":
            item.generation_claim_token = uuid.uuid4()
        elif mutation == "paused":
            item.hospital.status = "PAUSED"
        else:
            item.human_edited_at = NOW + timedelta(seconds=1)
            item.body = "human edited text"
            item.content_revision += 1
        db.commit()
    newer = next_claim(context)
    with sessions() as db:
        assert begin_generation_execution(db, item_id, reservation, newer, now=NOW) is None
        if mutation == "edited":
            assert db.get(ContentItem, item_id).body == "human edited text"


def test_provider_checkpoint_is_preserved_during_takeover(live):
    sessions, item_id, reservation, context, _ = live
    with sessions() as db:
        item, token = begin_generation_execution(db, item_id, reservation, context, now=NOW)
        assert write_back_generated_content(db, item_id=item_id,
            expected_revision=item.content_revision, expected_claim_token=token,
            values={"body": "already paid and saved", "title": "checkpoint"}) == 1
        db.commit()
    newer = next_claim(context)
    with sessions() as db:
        item, _ = begin_generation_execution(db, item_id, reservation, newer, now=NOW)
        assert item.body == "already paid and saved" and item.title == "checkpoint"


def test_process_exit_after_commit_is_recoverable(live):
    sessions, item_id, reservation, context, _ = live
    code = """
import os, sys, uuid
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.workers.generation_execution_claim import begin_generation_execution
from app.workers.generation_run_control import ExplicitRunContext
url, item, token, run, worker, version, now = sys.argv[1:]
with Session(create_engine(url), expire_on_commit=False) as db:
    context = ExplicitRunContext(uuid.UUID(run), worker, int(version))
    result = begin_generation_execution(db, uuid.UUID(item), uuid.UUID(token), context,
                                        now=datetime.fromisoformat(now))
    if result is None:
        raise RuntimeError("Initial execution was not claimed")
    os._exit(73)
"""
    child = subprocess.run([sys.executable, "-c", code, URL, str(item_id), str(reservation),
        str(context.run_id), context.worker_id, str(context.version), NOW.isoformat()],
        capture_output=True, text=True, timeout=20)
    assert child.returncode == 73, child.stderr
    newer = next_claim(context)
    with sessions() as db:
        assert begin_generation_execution(db, item_id, reservation, newer, now=NOW) is not None


def test_multiple_redeliveries_keep_original_reservation_and_reject_duplicates(live):
    sessions, item_id, reservation, context, _ = live
    seen = set()
    for _ in range(3):
        with sessions() as db:
            item, token = begin_generation_execution(db, item_id, reservation, context, now=NOW)
            assert token not in seen
            seen.add(token)
        with sessions() as duplicate:
            assert begin_generation_execution(duplicate, item_id, reservation, context, now=NOW) is None
        context = next_claim(context)


@pytest.mark.parametrize("field,value", [
    ("reservation_token", "wrong"), ("execution_token", "wrong"),
    ("worker_id", "wrong"), ("run_version", True), ("run_version", "2"),
    ("run_version", 9999), ("human_edited_at", "different-edit"),
])
def test_redelivery_rejects_corrupt_or_unrelated_lineage(live, field, value):
    sessions, item_id, reservation, context, run_id = live
    with sessions() as db:
        assert begin_generation_execution(db, item_id, reservation, context, now=NOW)
        run = db.get(OperationRun, run_id)
        run.request_payload = {**run.request_payload, "generation_execution": {
            **run.request_payload["generation_execution"], field: value}}
        db.commit()
    with sessions() as db:
        assert begin_generation_execution(db, item_id, reservation, next_claim(context), now=NOW) is None


def test_token_and_lineage_rollback_together_on_commit_failure(live, monkeypatch):
    sessions, item_id, reservation, context, run_id = live
    with sessions() as db:
        def interrupted_commit():
            db.flush()
            raise RuntimeError("simulated failure before commit")
        monkeypatch.setattr(db, "commit", interrupted_commit)
        with pytest.raises(RuntimeError, match="before commit"):
            begin_generation_execution(db, item_id, reservation, context, now=NOW)
        db.rollback()
    with sessions() as verify:
        assert verify.get(ContentItem, item_id).generation_claim_token == reservation
        assert "generation_execution" not in verify.get(OperationRun, run_id).request_payload
        assert begin_generation_execution(verify, item_id, reservation, context, now=NOW)


def test_stale_run_completion_cannot_close_new_execution(live):
    from types import SimpleNamespace

    from app.workers.generation_run_control import finish_explicit_run
    sessions, item_id, reservation, context, run_id = live
    with sessions() as db:
        assert begin_generation_execution(db, item_id, reservation, context, now=NOW)
    newer = next_claim(context)
    old_task = SimpleNamespace(request=SimpleNamespace(id=context.worker_id,
        headers={"operation_run_id": str(run_id)}, operation_run_claim_version=context.version))
    with sessions() as db:
        assert finish_explicit_run(db, old_task, item_id, OperationRunState.CANCELLED) is None
        assert db.get(OperationRun, run_id).state == "RUNNING"
        assert db.get(OperationRun, run_id).version == newer.version


async def test_authority_change_and_public_intent_commit_or_rollback_together(live):
    from test_geo_autonomy_hardening import AsyncDB

    from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
    from app.services.knowledge_changes import invalidate_source_authority
    from app.services.public_surface_intents import enqueue_public_surface_intent
    from app.utils.db_locks import acquire_hospital_advisory_lock_sync
    sessions, item_id, reservation, _, _ = live
    source_id = uuid.uuid4()
    with sessions() as db:
        item = db.get(ContentItem, item_id)
        base = HospitalContentPhilosophy(hospital_id=item.hospital_id, version=1,
            status=PhilosophyStatus.APPROVED, is_base=True, source_asset_ids=[str(source_id)])
        db.add(base)
        db.flush()
        item.content_philosophy_id = base.id
        item.essence_check_summary = {"generation_provenance": {
            "evidence_source_asset_ids": [str(source_id)]}}
        db.commit()
        original_revision = item.content_revision
        hospital_id, first = item.hospital_id, item.first_published_at
    for commit in (False, True):
        with sessions() as db:
            acquire_hospital_advisory_lock_sync(db, hospital_id)
            changed = await invalidate_source_authority(AsyncDB(db), hospital_id, source_id,
                                                        reason="SOURCE_EXCLUDED")
            assert changed == [item_id]
            intent = enqueue_public_surface_intent(db, db.get(Hospital, hospital_id), content_ids=changed)
            intent_id = intent.id
            db.commit() if commit else db.rollback()
        with sessions() as check:
            item = check.get(ContentItem, item_id)
            assert item.first_published_at == first
            assert (check.get(OperationRun, intent_id) is not None) == commit
            assert item.generation_claim_token == (None if commit else reservation)
            assert item.content_revision == original_revision + int(commit)
    with sessions() as stale:
        assert write_back_generated_content(stale, item_id=item_id,
            expected_revision=original_revision, expected_claim_token=reservation,
            values={"body": "withdrawn evidence"}) == 0


async def test_real_hospital_lock_serializes_date_updates(live):
    from app.utils.db_locks import acquire_hospital_advisory_lock_sync
    sessions, item_id, _, _, _ = live
    with sessions() as db:
        hospital_id = db.get(ContentItem, item_id).hospital_id
    acquired = threading.Event()
    def second_transaction():
        with sessions() as db:
            acquire_hospital_advisory_lock_sync(db, hospital_id)
            acquired.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with sessions() as first:
            acquire_hospital_advisory_lock_sync(first, hospital_id)
            future = pool.submit(second_transaction)
            assert not acquired.wait(timeout=0.15)
            first.commit()
        future.result(timeout=10)
    assert acquired.is_set()
