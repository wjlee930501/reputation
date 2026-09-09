"""Real transaction coverage for the active cap and additive schema."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import delete, func, inspect, select
from sqlalchemy.orm import Session

from app.models.director_delta import DirectorDelta
from app.models.hospital import Hospital
from app.services.director_delta import (
    ActiveDirectorDeltaLimit,
    DirectorDeltaInput,
    create_director_delta,
)


def test_concurrent_writers_cannot_exceed_active_cap(pg_engine):
    hospital_id = uuid.uuid4()
    payload = DirectorDeltaInput(source="DIRECTOR", avoid_messages=["feedback"])
    with Session(pg_engine) as db, db.begin():
        db.add(Hospital(id=hospital_id, name="Delta concurrency", slug=str(hospital_id)))
        db.flush()
        for _ in range(49):
            create_director_delta(db, hospital_id, payload)

    barrier = Barrier(2)

    def write():
        with Session(pg_engine) as db:
            barrier.wait(timeout=10)
            try:
                with db.begin():
                    create_director_delta(db, hospital_id, payload)
                return "created"
            except ActiveDirectorDeltaLimit:
                return "capped"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write) for _ in range(2)]
            assert sorted(f.result(timeout=15) for f in futures) == ["capped", "created"]
        with Session(pg_engine) as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(DirectorDelta)
                    .where(
                        DirectorDelta.hospital_id == hospital_id,
                        DirectorDelta.status == "ACTIVE",
                    )
                )
                == 50
            )
        schema = inspect(pg_engine)
        assert {c["name"] for c in schema.get_check_constraints("director_deltas")} == {
            "director_delta_source",
            "director_delta_status",
        }
        assert "ix_director_deltas_hospital_status_created" in {
            i["name"] for i in schema.get_indexes("director_deltas")
        }
    finally:
        with Session(pg_engine) as db, db.begin():
            db.execute(delete(Hospital).where(Hospital.id == hospital_id))
