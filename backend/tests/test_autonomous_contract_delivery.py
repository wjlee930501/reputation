"""Contract-delivery failure proofs; no providers or production calls."""

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import arrow
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_geo_autonomy_hardening import db as db
from test_geo_autonomy_hardening import seed

from app.core.celery_app import celery_app
from app.models.content import ContentItem
from app.models.hospital import HospitalStatus
from app.services.content_calendar import generate_monthly_slots
from app.workers import monthly_slots, tasks


@pytest.mark.parametrize("plan,total", [("PLAN_12", 12), ("PLAN_16", 16), ("PLAN_20", 20)])
def test_quota_repair_uses_remaining_days_even_without_a_preferred_weekday(plan, total):
    slots = generate_monthly_slots(
        plan, [0], arrow.get("2026-09-01"), date(2026, 9, 30), ensure_quota=True
    )
    assert len(slots) == total
    assert {slot[0] for slot in slots} == {date(2026, 9, 30)}
    assert [slot[2] for slot in slots] == list(range(1, total + 1))


@pytest.mark.parametrize(
    "days,start", [([], date(2026, 9, 30)), ([7], date(2026, 9, 30)), ([0], date(2026, 10, 1))]
)
def test_quota_repair_does_not_invent_a_valid_schedule(days, start):
    with pytest.raises(ValueError):
        generate_monthly_slots("PLAN_12", days, arrow.get("2026-09-01"), start, ensure_quota=True)


def test_strict_initial_schedule_still_rejects_no_remaining_preferred_day():
    with pytest.raises(ValueError):
        generate_monthly_slots("PLAN_12", [0], arrow.get("2026-09-01"), date(2026, 9, 30))


def test_failed_monthly_insertion_is_not_reported_as_already_complete(monkeypatch):
    hospital = SimpleNamespace(id="audit", name="audit", status=HospitalStatus.ACTIVE)
    schedule = SimpleNamespace(
        hospital=hospital,
        id="schedule",
        plan="PLAN_12",
        publish_days=list(range(7)),
        active_from=date(2026, 9, 1),
    )
    db = Mock()
    db.execute.return_value.all.return_value = []
    db.flush.side_effect = IntegrityError(
        "insert", {}, RuntimeError("unexpected foreign key failure")
    )
    monkeypatch.setattr(monthly_slots, "acquire_hospital_advisory_lock_sync", lambda *args: None)
    monkeypatch.setattr(monthly_slots, "build_gap_targets", lambda rows: [])

    @contextmanager
    def savepoint():
        yield

    db.begin_nested.side_effect = savepoint
    with pytest.raises(IntegrityError):
        monthly_slots.create_next_month_slots_for_schedule(
            db, schedule, arrow.get("2026-09-01"), date(2026, 9, 1), date(2026, 9, 30)
        )


def test_publisher_visits_healthy_rows_after_an_individual_failure(monkeypatch):
    db = Mock()
    db.execute.return_value.scalars.return_value.all.return_value = ["broken", "healthy", "last"]

    @contextmanager
    def session():
        yield db

    visited = []

    def publish(content_id):
        visited.append(content_id)
        if content_id == "broken":
            raise ValueError("one malformed legacy row")
        return None

    class RetryRequested(Exception):
        pass

    monkeypatch.setattr(tasks, "SyncSessionLocal", session)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *args: None)
    monkeypatch.setattr(tasks, "_auto_publish_one", publish)
    monkeypatch.setattr(
        tasks.morning_content_auto_publish, "retry", Mock(side_effect=RetryRequested)
    )
    with pytest.raises(RetryRequested):
        tasks.morning_content_auto_publish.run()
    assert visited == ["broken", "healthy", "last"]


def test_publisher_has_same_day_recovery_after_the_eight_am_tick():
    entry = celery_app.conf.beat_schedule["morning-content-auto-publish"]
    assert {8, 12, 18, 23} <= entry["schedule"].hour
    assert entry["schedule"].minute == {0}
    assert entry["task"] == "app.workers.tasks.morning_content_auto_publish"


def test_current_month_repair_keeps_new_work_inside_the_remaining_calendar(db):
    hospital, schedule = seed(db)
    original = {
        row.id: (row.scheduled_date, row.first_published_at)
        for row in db.scalars(select(ContentItem))
    }
    schedule.publish_days = [0]
    assert monthly_slots.create_next_month_slots_for_schedule(
        db,
        schedule,
        arrow.get("2026-09-01"),
        date(2026, 9, 1),
        date(2026, 9, 30),
        not_before=date(2026, 9, 30),
    )
    rows = list(db.scalars(select(ContentItem)))
    assert len(rows) == 12
    for row in rows:
        if row.id in original:
            assert (row.scheduled_date, row.first_published_at) == original[row.id]
        else:
            assert row.scheduled_date == date(2026, 9, 30)
    assert (
        monthly_slots.create_next_month_slots_for_schedule(
            db,
            schedule,
            arrow.get("2026-09-01"),
            date(2026, 9, 1),
            date(2026, 9, 30),
            not_before=date(2026, 9, 30),
        )
        is False
    )


def test_daytime_recovery_reuses_existing_bounded_pipeline_and_dispatch_purpose():
    night = celery_app.conf.beat_schedule["overnight-content-generation-recovery"]
    day = celery_app.conf.beat_schedule["daytime-content-generation-recovery"]
    assert day["task"] == night["task"]
    assert day["schedule"].hour == {12, 18, 22}
    assert day["schedule"].minute == {0}
    assert day["options"]["headers"].keys() == night["options"]["headers"].keys()
    assert "kwargs" not in day
