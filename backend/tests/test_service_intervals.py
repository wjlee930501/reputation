import uuid
from datetime import UTC, datetime, timedelta

from app.models.monthly_control import HospitalServiceInterval
from app.services.service_intervals import (
    ServiceIntervalProvenance,
    close_service_interval,
    open_service_interval,
)


class FakeDB:
    def __init__(self, current: HospitalServiceInterval | None = None):
        self.current = current
        self.added: list[HospitalServiceInterval] = []

    async def scalar(self, _stmt):
        return self.current

    def add(self, interval: HospitalServiceInterval):
        self.added.append(interval)
        self.current = interval


async def test_activation_opens_exactly_one_interval_on_replay():
    hospital_id = uuid.uuid4()
    started_at = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
    db = FakeDB()

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
    db = FakeDB()

    interval = await open_service_interval(db, hospital_id, ServiceIntervalProvenance.RESUME)

    assert interval.provenance == "RESUME"
    assert interval.hospital_id == hospital_id
