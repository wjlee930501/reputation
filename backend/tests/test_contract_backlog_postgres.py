"""Real database checks for orphaned contractual work and ownership boundaries."""

import os
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.workers.content_backlog_recovery import _stranded_content_stmt

TODAY = date(2026, 9, 17)


@pytest.fixture
def db():
    url = make_url(
        os.environ.get(
            "SYNC_DATABASE_URL",
            "postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test",
        )
    )
    assert url.database == "reputation_test", "Only the test database may be used"
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            yield session
        transaction.rollback()
    engine.dispose()


def seed(db, *, enabled=True):
    hospital = Hospital(
        id=uuid.uuid4(),
        name="Contract audit",
        slug="audit-" + uuid.uuid4().hex[:16],
        status=HospitalStatus.ACTIVE,
        site_live=True,
        plan=Plan.PLAN_12,
    )
    old = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[0, 2, 4],
        active_from=date(2026, 9, 1),
        is_active=False,
    )
    new = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan="PLAN_16",
        publish_days=[0, 2, 4],
        active_from=date(2026, 10, 1),
        is_active=enabled,
    )
    db.add(hospital)
    db.flush()
    db.add_all([old, new])
    db.flush()
    item = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        schedule_id=old.id,
        content_type=ContentType.HEALTH,
        sequence_no=1,
        total_count=12,
        scheduled_date=date(2026, 9, 1),
        status=ContentStatus.DRAFT,
    )
    db.add(item)
    db.flush()
    return hospital, old, item


def selected(db, hospital):
    return list(
        db.scalars(_stranded_content_stmt(TODAY).where(ContentItem.hospital_id == hospital.id))
    )


def test_replaced_schedule_does_not_strand_original_unfulfilled_work(db):
    hospital, old, item = seed(db)
    assert old.is_active is False
    assert selected(db, hospital) == [item]


def test_explicitly_disabled_schedules_are_not_silently_restarted(db):
    hospital, _, _ = seed(db, enabled=False)
    assert selected(db, hospital) == []


@pytest.mark.parametrize(
    "field", ["generation_claim_token", "human_edited_at", "first_published_at", "published_at"]
)
def test_backlog_does_not_move_claimed_human_or_previous_publication_rows(db, field):
    hospital, old, item = seed(db)
    old.is_active = True
    setattr(
        item,
        field,
        uuid.uuid4() if field == "generation_claim_token" else datetime(2026, 9, 1, tzinfo=UTC),
    )
    if field == "generation_claim_token":
        item.generation_claimed_at = datetime.now(UTC)
    db.flush()
    assert selected(db, hospital) == []


@pytest.mark.parametrize("status", [HospitalStatus.PAUSED, HospitalStatus.PENDING_DOMAIN])
def test_backlog_preserves_nonoperational_hospitals(db, status):
    hospital, old, _ = seed(db)
    hospital.status = status
    old.is_active = True
    db.flush()
    assert selected(db, hospital) == []


def test_original_carried_over_obligation_remains_recoverable(db):
    hospital, _, item = seed(db, enabled=False)
    item.carried_over_from = date(2026, 8, 25)
    db.flush()
    assert selected(db, hospital) == [item]


def test_expired_writer_claim_can_be_recovered_instead_of_stranding_forever(db):
    hospital, _, item = seed(db)
    item.generation_claim_token = uuid.uuid4()
    item.generation_claimed_at = datetime(2000, 1, 1, tzinfo=UTC)
    db.flush()
    assert selected(db, hospital) == [item]


def test_another_hospitals_enabled_schedule_cannot_restart_a_disabled_hospital(db):
    hospital, _, _ = seed(db, enabled=False)
    seed(db, enabled=True)
    assert selected(db, hospital) == []


def test_busy_calendar_lock_defers_backlog_without_waiting_or_mutating(db, monkeypatch):
    """Calendar saves lock hospital then rows; recovery must not wait in reverse."""
    from contextlib import contextmanager

    import arrow
    from sqlalchemy import text

    from app.utils.db_locks import acquire_hospital_advisory_lock_sync
    from app.workers import content_backlog_recovery as backlog

    hospital, _, item = seed(db)
    original_date = item.scheduled_date
    original_selector = backlog._stranded_content_stmt
    monkeypatch.setattr(backlog, "require_dispatch", lambda *args: None)
    monkeypatch.setattr(backlog.arrow, "now", lambda *args: arrow.get(TODAY))
    monkeypatch.setattr(
        backlog, "_stranded_content_stmt",
        lambda today: original_selector(today).where(ContentItem.hospital_id == hospital.id),
    )

    @contextmanager
    def current_session():
        yield db

    monkeypatch.setattr(backlog, "SyncSessionLocal", current_session)
    # Test-only bound: the old blocking order fails quickly instead of hanging CI.
    db.execute(text("SET LOCAL statement_timeout = '750ms'"))
    with Session(db.get_bind().engine) as calendar:
        acquire_hospital_advisory_lock_sync(calendar, hospital.id)
        result = backlog.reconcile.run()
        assert result["rescheduled"] == 0
        assert result["deferred_hospitals"] == 1
        assert item.scheduled_date == original_date
        calendar.rollback()

    # The same contract identity is recoverable once the calendar transaction ends.
    result = backlog.reconcile.run()
    assert result["rescheduled"] == 1
    assert result["deferred_hospitals"] == 0
    assert item.scheduled_date > TODAY
