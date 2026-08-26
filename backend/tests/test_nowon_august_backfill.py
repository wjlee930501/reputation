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
from unittest.mock import Mock  # noqa: E402

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
        self.hospitals = hospital if isinstance(hospital, list) else [hospital]
        self.hospital = self.hospitals[0]
        self.selected_hospital = None
        self.schedule = schedule
        self.items = list(items)
        self.commit_calls = 0

    def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is Hospital:
            query_values = set(stmt.compile().params.values())
            matches = [
                hospital
                for hospital in self.hospitals
                if hospital.id in query_values or hospital.name in query_values
            ]
            self.selected_hospital = matches[0] if matches else None
            return _Result(matches[:1])
        if entity is ContentSchedule:
            hospital = self.selected_hospital
            matches_schedule = (
                hospital is not None
                and self.schedule is not None
                and self.schedule.hospital_id == hospital.id
                and self.schedule.is_active
            )
            return _Result([self.schedule] if matches_schedule else [])
        if entity is ContentItem:
            hospital = self.selected_hospital
            return _Result(
                [
                    item
                    for item in self.items
                    if hospital is not None
                    and item.hospital_id == hospital.id
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

    created = backfill.backfill_nowon_august_2026_slots()

    assert created == 9
    assert db.commit_calls == 1
    assert [
        (item.id, item.scheduled_date, item.sequence_no, item.status) for item in db.items[:3]
    ] == published_before
    new_items = db.items[3:]
    assert all(item.status == ContentStatus.DRAFT for item in new_items)
    assert all(item.total_count == 12 for item in new_items)
    assert [item.sequence_no for item in new_items] == list(range(4, 13))
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
    assert all(item.content_type != ContentType.NOTICE for item in new_items)
    assert generate_monthly_slots.call_count == 0
    assert not hasattr(backfill, "generate_monthly_slots")
    assert regenerate_apply_async.call_count == 9
    assert [
        call.kwargs["args"][0] for call in regenerate_apply_async.call_args_list
    ] == [str(item.id) for item in new_items]
    morning_apply_async.assert_called_once_with(
        queue="content",
        headers={"purpose": "morning-content-auto-publish", "target": None},
    )

    assert backfill.backfill_nowon_august_2026_slots() == 0
    assert len(db.items) == 12
    assert regenerate_apply_async.call_count == 9
    assert morning_apply_async.call_count == 1


def test_backfill_is_noop_when_august_already_has_twelve_items(monkeypatch):
    db = _db_with_existing(existing_count=12)
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 0
    assert db.commit_calls == 0
    assert dispatched == []


def test_backfill_subtracts_existing_planned_day_occupancy(monkeypatch):
    db = _db_with_existing()
    db.items.append(
        _content_item(
            db.hospital.id,
            db.schedule.id,
            date(2026, 8, 26),
            4,
            ContentStatus.DRAFT,
            ContentType.COLUMN,
        )
    )
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 8

    new_items = db.items[4:]
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
    assert len(db.items) == 12
    assert dispatched == [str(item.id) for item in new_items]


def test_backfill_prefers_exact_hospital_id_over_name_match(monkeypatch):
    name_match = SimpleNamespace(id=uuid.uuid4(), name=backfill.NOWON_HOSPITAL_NAME)
    id_match = SimpleNamespace(id=backfill.NOWON_HOSPITAL_ID, name="ID 우선 병원")
    schedule = SimpleNamespace(id=uuid.uuid4(), hospital_id=id_match.id, is_active=True)
    items = [
        _content_item(
            id_match.id,
            schedule.id,
            day,
            sequence,
            ContentStatus.PUBLISHED,
            content_type,
        )
        for day, sequence, content_type in zip(
            [date(2026, 8, 20), date(2026, 8, 23), date(2026, 8, 24)],
            [1, 2, 3],
            [ContentType.FAQ, ContentType.DISEASE, ContentType.TREATMENT],
        )
    ]
    db = _BackfillDB([name_match, id_match], schedule, items)
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 9
    assert db.selected_hospital is id_match
    assert all(item.hospital_id == id_match.id for item in db.items)
    assert len(dispatched) == 9


def test_backfill_is_noop_for_other_hospital(monkeypatch):
    db = _db_with_existing(hospital_name="다른의원")
    dispatched: list[str] = []
    monkeypatch.setattr(backfill, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(backfill, "_enqueue_created_drafts", dispatched.extend)

    assert backfill.backfill_nowon_august_2026_slots() == 0
    assert len(db.items) == 3
    assert db.commit_calls == 0
    assert dispatched == []
