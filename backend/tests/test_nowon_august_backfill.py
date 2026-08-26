"""Always-run unit coverage for the 노원탑 August 2026 one-off close."""

import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

import uuid  # noqa: E402
from collections import Counter  # noqa: E402
from contextlib import nullcontext  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from app.models.content import (  # noqa: E402
    ContentItem,
    ContentSchedule,
    ContentStatus,
    ContentType,
)
from app.models.hospital import Hospital  # noqa: E402
from app.workers import nowon_august_backfill as backfill  # noqa: E402


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


class _BackfillDB:
    def __init__(self, hospital, schedule, items):
        self.hospital = hospital
        self.schedule = schedule
        self.items = list(items)
        self.commit_calls = 0

    def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is Hospital:
            matches_gate = (
                self.hospital.name == backfill.NOWON_HOSPITAL_NAME
                or self.hospital.id == backfill.NOWON_HOSPITAL_ID
            )
            return _Result([self.hospital] if matches_gate else [])
        if entity is ContentSchedule:
            matches_schedule = (
                self.schedule is not None
                and self.schedule.hospital_id == self.hospital.id
                and self.schedule.is_active
            )
            return _Result([self.schedule] if matches_schedule else [])
        if entity is ContentItem:
            return _Result(
                [
                    item
                    for item in self.items
                    if item.hospital_id == self.hospital.id
                    and backfill.AUGUST_START <= item.scheduled_date <= backfill.AUGUST_END
                    and item.status != ContentStatus.CANCELLED
                ]
            )
        raise AssertionError(f"unexpected query entity: {entity}")

    def begin_nested(self):
        return nullcontext()

    def add_all(self, items):
        self.items.extend(items)

    def flush(self):
        for item in self.items:
            if item.id is None:
                item.id = uuid.uuid4()

    def commit(self):
        self.commit_calls += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _content_item(hospital_id, schedule_id, day, sequence, status, content_type):
    return ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        schedule_id=schedule_id,
        content_type=content_type,
        sequence_no=sequence,
        total_count=20,
        scheduled_date=day,
        status=status,
    )


def _db_with_existing(existing_count=3, *, hospital_name=backfill.NOWON_HOSPITAL_NAME):
    # A non-production UUID proves that a name-only match is sufficient.
    hospital = SimpleNamespace(id=uuid.uuid4(), name=hospital_name)
    schedule = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        is_active=True,
    )
    initial_days = [date(2026, 8, 20), date(2026, 8, 23), date(2026, 8, 24)]
    content_types = [ContentType.FAQ, ContentType.DISEASE, ContentType.TREATMENT]
    items = []
    for index in range(existing_count):
        items.append(
            _content_item(
                hospital.id,
                schedule.id,
                initial_days[index] if index < 3 else date(2026, 8, 1) + timedelta(days=index),
                index + 1,
                ContentStatus.PUBLISHED if index < 3 else ContentStatus.DRAFT,
                content_types[index % len(content_types)],
            )
        )
    return _BackfillDB(hospital, schedule, items)


def test_backfill_creates_nine_drafts_without_touching_published_rows(monkeypatch):
    db = _db_with_existing()
    published_before = [
        (item.id, item.scheduled_date, item.sequence_no, item.status) for item in db.items
    ]
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    created = backfill.backfill_nowon_august_2026_slots()

    assert created == 9
    assert db.commit_calls == 1
    assert [
        (item.id, item.scheduled_date, item.sequence_no, item.status) for item in db.items[:3]
    ] == published_before
    new_items = db.items[3:]
    assert len(dispatched) == 9
    assert dispatched == [str(item.id) for item in new_items]
    assert all(item.status == ContentStatus.DRAFT for item in new_items)
    assert all(item.total_count == 12 for item in new_items)
    assert [item.sequence_no for item in new_items] == list(range(4, 13))
    assert all(date(2026, 8, 26) <= item.scheduled_date <= date(2026, 8, 31) for item in new_items)
    date_counts = Counter(item.scheduled_date for item in new_items)
    assert max(date_counts.values()) == 2
    assert {day.day for day, count in date_counts.items() if count == 2} == {26, 28, 31}
    assert all(item.content_type != ContentType.NOTICE for item in new_items)

    assert backfill.backfill_nowon_august_2026_slots() == 0
    assert len(db.items) == 12
    assert len(dispatched) == 9


def test_backfill_is_noop_when_august_already_has_twelve_items(monkeypatch):
    db = _db_with_existing(existing_count=12)
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 0
    assert db.commit_calls == 0
    assert dispatched == []


def test_backfill_is_noop_for_other_hospital(monkeypatch):
    db = _db_with_existing(hospital_name="다른의원")
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 0
    assert len(db.items) == 3
    assert db.commit_calls == 0
    assert dispatched == []
