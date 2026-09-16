"""A post-commit incident failure must not duplicate/mask a generation outcome."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.workers.generation_run_control import create_item_run


def _fixture(engine):
    with Session(engine) as db:
        h = Hospital(name="Synthetic child idempotency", slug=f"child-{uuid.uuid4()}")
        db.add(h)
        db.flush()
        parent = OperationRun(
            hospital_id=h.id,
            operation_type="GENERATE_CONTENT_ITEM",
            state="SUCCEEDED",
            idempotency_key=str(uuid.uuid4()),
        )
        db.add(parent)
        db.commit()
        return h.id, parent.id, uuid.uuid4()


def _write(db, ids, *, code="MISSING_APPROVED_ESSENCE", attempt="final-1"):
    h, parent, item = ids
    return create_item_run(
        db,
        hospital_id=h,
        parent_run_id=parent,
        item_id=item,
        operation_type="REGENERATE_CONTENT",
        state=OperationRunState.FAILED,
        result={"state": "FAILED"},
        safe_error_code=code,
        attempt_kind=attempt,
    )


def test_repeated_child_keeps_first_committed_outcome(pg_engine):
    ids = _fixture(pg_engine)
    with Session(pg_engine) as db:
        first = _write(db, ids)
        repeated = _write(db, ids, code="GENERATION_FAILED")
        assert repeated.id == first.id
        assert repeated.safe_error_code == "MISSING_APPROVED_ESSENCE"
        assert (
            db.scalar(
                select(func.count())
                .select_from(OperationRun)
                .where(OperationRun.parent_run_id == ids[1])
            )
            == 1
        )
        assert _write(db, ids, attempt="final-2").id != first.id


def test_concurrent_child_writers_share_one_committed_result(pg_engine):
    ids = _fixture(pg_engine)
    barrier = Barrier(2)

    def write():
        with Session(pg_engine) as db:
            barrier.wait(timeout=10)
            return _write(db, ids).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write) for _ in range(2)]
        assert len({future.result(timeout=15) for future in futures}) == 1


def test_non_idempotency_integrity_errors_still_raise(pg_engine):
    import pytest
    from sqlalchemy.exc import IntegrityError

    h, parent, item = _fixture(pg_engine)
    with Session(pg_engine) as db:
        with pytest.raises(IntegrityError):
            _write(db, (h, uuid.uuid4(), item))
        # Only the savepoint failed: unrelated valid work can still complete.
        assert _write(db, (h, parent, item)).parent_run_id == parent
