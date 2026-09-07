"""Real PostgreSQL checks for publication-transaction IndexNow durability."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.operations import OperationRun, OperationRunState
from app.services import indexnow
from app.workers import indexnow_retry


def test_concurrent_duplicate_enqueue_is_a_noop_instead_of_integrity_failure(pg_engine) -> None:
    base = "https://concurrent-indexnow.example.com"
    urls = [f"{base}/contents/same"]
    barrier = Barrier(2)

    def enqueue() -> object:
        with Session(pg_engine) as db:
            barrier.wait(timeout=5)
            run_id = indexnow.enqueue_urls_sync(
                db,
                base_url=base,
                urls=urls,
                revision="content-same:17",
                slug="concurrent-indexnow",
                content_id="same",
            )
            db.commit()
            return run_id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(enqueue)
            second_future = pool.submit(enqueue)
            first = first_future.result(timeout=10)
            second = second_future.result(timeout=10)

        assert first == second
        with Session(pg_engine) as db:
            count = db.scalar(
                select(func.count(OperationRun.id)).where(OperationRun.id == first)
            )
            assert count == 1
    finally:
        with Session(pg_engine) as db:
            if "first" in locals():
                db.execute(delete(OperationRun).where(OperationRun.id == first))
                db.commit()


def test_enqueue_rollback_does_not_leave_an_external_delivery_intent(pg_engine) -> None:
    base = "https://rollback-indexnow.example.com"
    with Session(pg_engine) as db:
        run_id = indexnow.enqueue_urls_sync(
            db,
            base_url=base,
            urls=[f"{base}/contents/rollback"],
            revision="content-rollback:3",
        )
        db.rollback()

    with Session(pg_engine) as db:
        assert db.get(OperationRun, run_id) is None


def test_not_due_old_rows_cannot_starve_a_new_due_intent(pg_engine, monkeypatch) -> None:
    base = "https://due-indexnow.example.com"
    now = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
    run_ids = []
    due_id = None
    try:
        with Session(pg_engine) as db:
            for value in range(25):
                run_id = indexnow.enqueue_urls_sync(
                    db,
                    base_url=base,
                    urls=[f"{base}/contents/not-due-{value}"],
                    revision=f"not-due:{value}",
                )
                run = db.get(OperationRun, run_id)
                assert run is not None
                run.attempt_count = 2
                run.heartbeat_at = now
                run_ids.append(run_id)
            due_id = indexnow.enqueue_urls_sync(
                db,
                base_url=base,
                urls=[f"{base}/contents/due"],
                revision="due-now",
            )
            run_ids.append(due_id)
            db.commit()

        monkeypatch.setattr(
            indexnow_retry,
            "SyncSessionLocal",
            sessionmaker(bind=pg_engine, expire_on_commit=False),
        )
        claimed, terminal = indexnow_retry._claim_due(now, "claim-due-row")

        assert terminal == 0
        assert [intent.run_id for intent in claimed] == [due_id]
        with Session(pg_engine) as db:
            not_due_states = db.scalars(
                select(OperationRun.state).where(
                    OperationRun.id.in_(run_ids[:-1])
                )
            ).all()
            assert set(not_due_states) == {OperationRunState.REQUESTED.value}
    finally:
        with Session(pg_engine) as db:
            if run_ids:
                db.execute(delete(OperationRun).where(OperationRun.id.in_(run_ids)))
                db.commit()
