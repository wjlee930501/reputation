import uuid
from datetime import UTC, datetime, timedelta

import anyio

from app.models.hospital import Hospital
from app.models.monthly_control import HospitalServiceInterval
from app.services.service_intervals import (
    ServiceIntervalProvenance,
    close_service_interval,
    open_service_interval,
)


class FakeDB:
    def __init__(
        self,
        current: HospitalServiceInterval | None = None,
        *,
        hospital_id: uuid.UUID | None = None,
    ):
        self.current = current
        self.hospital_id = hospital_id or (current.hospital_id if current else uuid.uuid4())
        self.added: list[HospitalServiceInterval] = []
        self.selected_entities: list[type] = []

    async def scalar(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        self.selected_entities.append(entity)
        if entity is Hospital:
            return self.hospital_id
        return self.current

    def add(self, interval: HospitalServiceInterval):
        self.added.append(interval)
        self.current = interval


class ConcurrentState:
    """Mutable shared store modelling rows visible after the parent lock is held."""

    def __init__(self):
        self.lock = anyio.Lock()
        self.first_locked = anyio.Event()
        self.current: HospitalServiceInterval | None = None
        self.added: list[HospitalServiceInterval] = []


class ConcurrentDB:
    def __init__(self, state: ConcurrentState, hospital_id: uuid.UUID, *, first: bool):
        self.state = state
        self.hospital_id = hospital_id
        self.first = first

    async def scalar(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is Hospital:
            await self.state.lock.acquire()
            if self.first:
                self.state.first_locked.set()
            return self.hospital_id
        return self.state.current

    def add(self, interval: HospitalServiceInterval):
        self.state.added.append(interval)
        self.state.current = interval

    async def commit(self):
        self.state.lock.release()


async def test_activation_opens_exactly_one_interval_on_replay():
    hospital_id = uuid.uuid4()
    started_at = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
    db = FakeDB(hospital_id=hospital_id)

    first = await open_service_interval(
        db, hospital_id, ServiceIntervalProvenance.ACTIVATION, occurred_at=started_at
    )
    replay = await open_service_interval(
        db,
        hospital_id,
        ServiceIntervalProvenance.ACTIVATION,
        occurred_at=started_at + timedelta(minutes=1),
    )

    assert replay is first
    assert db.added == [first]
    assert first.provenance == "ACTIVATION"
    assert first.started_at == started_at
    assert first.ended_at is None
    assert db.selected_entities == [
        Hospital,
        HospitalServiceInterval,
        Hospital,
        HospitalServiceInterval,
    ]


async def test_pause_closes_open_interval_once_without_rewriting_provenance():
    started_at = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
    ended_at = started_at + timedelta(hours=1)
    interval = HospitalServiceInterval(
        hospital_id=uuid.uuid4(),
        started_at=started_at,
        provenance="LEGACY_CUTOVER",
    )
    db = FakeDB(interval)

    closed = await close_service_interval(db, interval.hospital_id, occurred_at=ended_at)
    db.current = None
    replay = await close_service_interval(
        db, interval.hospital_id, occurred_at=ended_at + timedelta(minutes=1)
    )

    assert closed is interval
    assert replay is None
    assert interval.ended_at == ended_at
    assert interval.provenance == "LEGACY_CUTOVER"


async def test_resume_opens_interval_with_resume_provenance():
    hospital_id = uuid.uuid4()
    db = FakeDB(hospital_id=hospital_id)

    interval = await open_service_interval(db, hospital_id, ServiceIntervalProvenance.RESUME)

    assert interval.provenance == "RESUME"
    assert interval.hospital_id == hospital_id


async def test_concurrent_first_open_calls_converge_after_parent_lock():
    hospital_id = uuid.uuid4()
    state = ConcurrentState()
    first_db = ConcurrentDB(state, hospital_id, first=True)
    second_db = ConcurrentDB(state, hospital_id, first=False)
    results: list[HospitalServiceInterval] = []

    async def _open(db: ConcurrentDB, provenance: ServiceIntervalProvenance):
        interval = await open_service_interval(db, hospital_id, provenance)
        results.append(interval)
        await db.commit()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(_open, first_db, ServiceIntervalProvenance.ACTIVATION)
        await state.first_locked.wait()
        task_group.start_soon(_open, second_db, ServiceIntervalProvenance.RESUME)

    assert len(results) == 2
    assert results[0] is results[1]
    assert state.added == [results[0]]
    assert results[0].provenance == "ACTIVATION"
    assert results[0].ended_at is None
