"""Always-run unit coverage for the 노원탑 August 2026 one-off close."""

import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

import uuid  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import Mock  # noqa: E402

from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.models.content import (  # noqa: E402
    ContentItem,
    ContentSchedule,
    ContentStatus,
    ContentType,
)
from app.models.hospital import Hospital  # noqa: E402
from app.services import content_calendar  # noqa: E402
from app.workers import nowon_august_backfill as backfill  # noqa: E402
from app.workers import tasks  # noqa: E402

ACTIVE_SCHEDULE_ID = uuid.UUID("19a2e255-1111-4111-8111-111111111111")
INACTIVE_SCHEDULE_ID = uuid.UUID("9834e123-2222-4222-8222-222222222222")
SEPTEMBER_LOCAL_ID = uuid.UUID("3bc8671e-3333-4333-8333-333333333333")


class _Scalars:
    def __init__(self, items):
        self._items = items

    def first(self):
        return self._items[0] if self._items else None

    def all(self):
        return list(self._items)


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _Scalars(self._items)


class _NestedTransaction:
    def __init__(self, db):
        self._db = db
        self._start = 0

    def __enter__(self):
        assert self._db._savepoint_start is None
        self._start = len(self._db.items)
        self._db._savepoint_start = self._start
        return self

    def __exit__(self, exc_type, *_exc):
        if exc_type is not None:
            del self._db.items[self._start :]
        self._db._savepoint_start = None
        return False


class _BackfillDB:
    def __init__(self, hospital, schedules, items):
        self.hospitals = hospital if isinstance(hospital, list) else [hospital]
        self.hospital = self.hospitals[0]
        self.selected_hospital = None
        self.schedules = list(schedules)
        self.items = list(items)
        self.commit_calls = 0
        self.flush_calls = 0
        self.fail_next_flush = False
        self._savepoint_start = None

    def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        compiled = stmt.compile()
        query_values = set(compiled.params.values())
        sql = str(compiled)

        if entity is Hospital:
            matches = [
                hospital
                for hospital in self.hospitals
                if hospital.id in query_values or hospital.name in query_values
            ]
            self.selected_hospital = matches[0] if matches else None
            return _Result(matches[:1])

        if entity is ContentSchedule:
            matches = [
                schedule
                for schedule in self.schedules
                if self.selected_hospital is not None
                and schedule.hospital_id == self.selected_hospital.id
            ]
            if "content_schedules.is_active IS true" in sql:
                matches = [schedule for schedule in matches if schedule.is_active]
            matches.sort(
                key=lambda schedule: (schedule.created_at, str(schedule.id)),
                reverse=True,
            )
            return _Result(matches[:1])

        if entity is ContentItem:
            matches = [
                item
                for item in self.items
                if self.selected_hospital is not None
                and item.hospital_id == self.selected_hospital.id
            ]
            date_values = [value for value in query_values if isinstance(value, date)]
            if date_values:
                matches = [
                    item
                    for item in matches
                    if min(date_values) <= item.scheduled_date <= max(date_values)
                ]
            schedule_ids = {schedule.id for schedule in self.schedules} & query_values
            if schedule_ids:
                matches = [item for item in matches if item.schedule_id in schedule_ids]
            if ContentStatus.CANCELLED in query_values and "!=" in sql:
                matches = [item for item in matches if item.status != ContentStatus.CANCELLED]
            return _Result(matches)

        raise AssertionError(f"unexpected query entity: {entity}")

    def begin_nested(self):
        return _NestedTransaction(self)

    def add(self, item):
        assert self._savepoint_start is not None
        self.items.append(item)

    def flush(self):
        self.flush_calls += 1
        if self.fail_next_flush:
            self.fail_next_flush = False
            raise IntegrityError("insert", {}, Exception("forced duplicate slot"))

        assert self._savepoint_start is not None
        existing_keys = {
            (item.schedule_id, item.scheduled_date, item.sequence_no)
            for item in self.items[: self._savepoint_start]
        }
        for item in self.items[self._savepoint_start :]:
            key = (item.schedule_id, item.scheduled_date, item.sequence_no)
            if key in existing_keys:
                raise IntegrityError("insert", {}, Exception("duplicate slot"))
            existing_keys.add(key)
            if item.id is None:
                item.id = uuid.uuid4()

    def commit(self):
        self.commit_calls += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _schedule(schedule_id, hospital_id, *, active, plan, active_from, created_at):
    return SimpleNamespace(
        id=schedule_id,
        hospital_id=hospital_id,
        plan=plan,
        publish_days=[0, 1, 2, 3, 4],
        active_from=active_from,
        is_active=active,
        created_at=created_at,
    )


def _content_item(
    hospital_id,
    schedule_id,
    day,
    sequence,
    status,
    content_type,
    *,
    item_id=None,
    body=None,
):
    return ContentItem(
        id=item_id or uuid.uuid4(),
        hospital_id=hospital_id,
        schedule_id=schedule_id,
        content_type=content_type,
        sequence_no=sequence,
        total_count=12,
        scheduled_date=day,
        status=status,
        body=body,
    )


def _production_db(
    *,
    hospital_id=backfill.NOWON_HOSPITAL_ID,
    hospital_name=backfill.NOWON_HOSPITAL_NAME,
):
    hospital = SimpleNamespace(id=hospital_id, name=hospital_name)
    active = _schedule(
        ACTIVE_SCHEDULE_ID,
        hospital.id,
        active=True,
        plan="PLAN_20",
        active_from=date(2026, 9, 1),
        created_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
    )
    inactive = _schedule(
        INACTIVE_SCHEDULE_ID,
        hospital.id,
        active=False,
        plan="PLAN_12",
        active_from=date(2026, 8, 20),
        created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    items = [
        _content_item(
            hospital.id,
            inactive.id,
            date(2026, 8, 20),
            1,
            ContentStatus.PUBLISHED,
            ContentType.HEALTH,
        ),
        _content_item(
            hospital.id,
            inactive.id,
            date(2026, 8, 23),
            4,
            ContentStatus.PUBLISHED,
            ContentType.DISEASE,
        ),
        _content_item(
            hospital.id,
            inactive.id,
            date(2026, 8, 24),
            5,
            ContentStatus.PUBLISHED,
            ContentType.TREATMENT,
        ),
        _content_item(
            hospital.id,
            inactive.id,
            date(2026, 9, 1),
            2,
            ContentStatus.DRAFT,
            ContentType.LOCAL,
            item_id=SEPTEMBER_LOCAL_ID,
            body="이미 생성된 9월 지역 콘텐츠",
        ),
    ]
    return _BackfillDB(hospital, [active, inactive], items), active, inactive


def _published_snapshot(items):
    return [
        (
            item.id,
            item.scheduled_date,
            item.sequence_no,
            item.status,
            item.schedule_id,
            item.content_type,
        )
        for item in items
        if item.status == ContentStatus.PUBLISHED
    ]


def test_backfill_attaches_nine_drafts_to_active_schedule(monkeypatch):
    db, active, inactive = _production_db()
    initial_ids = {item.id for item in db.items}
    published_before = _published_snapshot(db.items)
    generate_monthly_slots = Mock()
    regenerate_apply_async = Mock()
    morning_apply_async = Mock()
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(content_calendar, "generate_monthly_slots", generate_monthly_slots)
    monkeypatch.setattr(tasks.regenerate_content_item, "apply_async", regenerate_apply_async)
    monkeypatch.setattr(tasks.morning_content_auto_publish, "apply_async", morning_apply_async)
    monkeypatch.setattr(
        backfill,
        "build_dispatch_headers",
        lambda purpose, target_id=None: {"purpose": purpose, "target": target_id},
    )

    assert backfill.backfill_nowon_august_2026_slots() == 9

    new_items = [item for item in db.items if item.id not in initial_ids]
    assert db.commit_calls == 1
    assert len(new_items) == 9
    assert all(item.schedule_id == active.id for item in new_items)
    assert all(item.schedule_id != inactive.id for item in new_items)
    assert all(item.status == ContentStatus.DRAFT for item in new_items)
    assert all(item.total_count == 12 for item in new_items)
    assert Counter(item.scheduled_date for item in new_items) == Counter(
        {
            date(2026, 8, 26): 2,
            date(2026, 8, 27): 2,
            date(2026, 8, 28): 2,
            date(2026, 8, 29): 1,
            date(2026, 8, 30): 1,
            date(2026, 8, 31): 1,
        }
    )
    assert Counter(item.content_type for item in new_items) == Counter(
        {
            ContentType.FAQ: 3,
            ContentType.DISEASE: 2,
            ContentType.TREATMENT: 1,
            ContentType.COLUMN: 2,
            ContentType.LOCAL: 1,
        }
    )
    assert _published_snapshot(db.items) == published_before
    september_local = next(item for item in db.items if item.id == SEPTEMBER_LOCAL_ID)
    assert (
        september_local.scheduled_date,
        september_local.sequence_no,
        september_local.status,
        september_local.schedule_id,
        september_local.content_type,
        september_local.body,
    ) == (
        date(2026, 9, 1),
        2,
        ContentStatus.DRAFT,
        inactive.id,
        ContentType.LOCAL,
        "이미 생성된 9월 지역 콘텐츠",
    )
    assert generate_monthly_slots.call_count == 0
    assert not hasattr(backfill, "generate_monthly_slots")
    assert [
        call.kwargs["args"][0] for call in regenerate_apply_async.call_args_list
    ] == [str(item.id) for item in new_items]
    assert str(SEPTEMBER_LOCAL_ID) not in {
        call.kwargs["args"][0] for call in regenerate_apply_async.call_args_list
    }
    morning_apply_async.assert_called_once_with(
        queue="content",
        headers={"purpose": "morning-content-auto-publish", "target": None},
    )

    assert backfill.backfill_nowon_august_2026_slots() == 0
    assert len(db.items) == 13
    assert db.commit_calls == 1
    assert regenerate_apply_async.call_count == 9
    assert morning_apply_async.call_count == 1


def test_september_local_is_excluded_from_august_count_and_dispatch(monkeypatch):
    db, _active, _inactive = _production_db()
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert len([item for item in db.items if item.scheduled_date.month == 8]) == 3
    assert backfill.backfill_nowon_august_2026_slots() == 9
    assert len(dispatched) == 9
    assert str(SEPTEMBER_LOCAL_ID) not in dispatched


def test_inactive_schedule_august_drafts_do_not_satisfy_idempotency(monkeypatch):
    db, active, inactive = _production_db()
    for sequence, planned_date in enumerate(backfill.PLANNED_DATES, start=1):
        db.items.append(
            _content_item(
                db.hospital.id,
                inactive.id,
                planned_date,
                sequence,
                ContentStatus.DRAFT,
                ContentType.FAQ,
            )
        )
    initial_ids = {item.id for item in db.items}
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 9
    created = [item for item in db.items if item.id not in initial_ids]
    assert len(created) == 9
    assert all(item.schedule_id == active.id for item in created)
    assert len(dispatched) == 9


def test_backfill_is_noop_when_active_schedule_has_all_nine_planned_cells(monkeypatch):
    db, active, _inactive = _production_db()
    for sequence, planned_date in enumerate(backfill.PLANNED_DATES, start=1):
        db.items.append(
            _content_item(
                db.hospital.id,
                active.id,
                planned_date,
                sequence,
                ContentStatus.DRAFT,
                ContentType.FAQ,
            )
        )
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 0
    assert db.commit_calls == 0
    assert dispatched == []


def test_backfill_subtracts_live_active_planned_day_occupancy(monkeypatch):
    db, active, _inactive = _production_db()
    occupied = _content_item(
        db.hospital.id,
        active.id,
        date(2026, 8, 26),
        7,
        ContentStatus.DRAFT,
        ContentType.COLUMN,
    )
    db.items.append(occupied)
    initial_ids = {item.id for item in db.items}
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 8

    new_items = [item for item in db.items if item.id not in initial_ids]
    assert Counter(item.scheduled_date for item in new_items) == Counter(
        {
            date(2026, 8, 26): 1,
            date(2026, 8, 27): 2,
            date(2026, 8, 28): 2,
            date(2026, 8, 29): 1,
            date(2026, 8, 30): 1,
            date(2026, 8, 31): 1,
        }
    )
    assert all(item.schedule_id == active.id for item in new_items)
    assert dispatched == [str(item.id) for item in new_items]


def test_cancelled_unique_and_one_integrity_error_do_not_zero_batch(monkeypatch):
    db, active, _inactive = _production_db()
    cancelled = _content_item(
        db.hospital.id,
        active.id,
        date(2026, 8, 26),
        1,
        ContentStatus.CANCELLED,
        ContentType.FAQ,
    )
    db.items.append(cancelled)
    initial_ids = {item.id for item in db.items}
    db.fail_next_flush = True
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 8

    created = [item for item in db.items if item.id not in initial_ids]
    assert len(created) == 8
    assert db.flush_calls == 9
    assert db.commit_calls == 1
    assert created
    assert all(item.schedule_id == active.id for item in created)
    assert all(
        (item.schedule_id, item.scheduled_date, item.sequence_no)
        != (cancelled.schedule_id, cancelled.scheduled_date, cancelled.sequence_no)
        for item in created
    )
    assert cancelled in db.items
    assert dispatched == [str(item.id) for item in created]


def test_backfill_prefers_exact_hospital_id_over_name_match(monkeypatch):
    db, active, _inactive = _production_db()
    name_match = SimpleNamespace(id=uuid.uuid4(), name=backfill.NOWON_HOSPITAL_NAME)
    db.hospitals = [name_match, db.hospital]
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 9
    assert db.selected_hospital is db.hospital
    assert all(item.hospital_id == backfill.NOWON_HOSPITAL_ID for item in db.items)
    assert all(
        item.schedule_id == active.id
        for item in db.items
        if str(item.id) in dispatched
    )


def test_backfill_uses_name_when_exact_hospital_id_is_absent(monkeypatch):
    hospital_id = uuid.uuid4()
    db, _active, _inactive = _production_db(hospital_id=hospital_id)
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 9
    assert db.selected_hospital is db.hospital
    assert all(item.hospital_id == hospital_id for item in db.items)


def test_backfill_is_noop_for_other_hospital(monkeypatch):
    db, _active, _inactive = _production_db(
        hospital_id=uuid.uuid4(),
        hospital_name="다른의원",
    )
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 0
    assert len(db.items) == 4
    assert db.commit_calls == 0
    assert dispatched == []


def test_backfill_is_noop_without_active_schedule(monkeypatch):
    db, _active, _inactive = _production_db()
    for schedule in db.schedules:
        schedule.is_active = False
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 0
    assert db.commit_calls == 0
    assert dispatched == []
