from datetime import date

from sqlalchemy.dialects import postgresql

from app.core.celery_app import REDBEAT_SCHEDULE_VERSION, celery_app
from app.workers.content_backlog_recovery import (
    _next_available_dates,
    _stranded_content_stmt,
)


def test_recovery_uses_distinct_future_gaps_without_moving_planned_dates():
    occupied = {date(2026, 8, 21), date(2026, 8, 25), date(2026, 8, 28)}

    recovery_dates = _next_available_dates(
        today=date(2026, 8, 18),
        occupied_dates=occupied,
        count=5,
    )

    assert recovery_dates == [
        date(2026, 8, 19),
        date(2026, 8, 20),
        date(2026, 8, 22),
        date(2026, 8, 23),
        date(2026, 8, 24),
    ]
    assert {date(2026, 8, 21), date(2026, 8, 25), date(2026, 8, 28)} <= occupied


def test_recovery_selector_is_bounded_to_due_unpublished_active_content():
    compiled = str(
        _stranded_content_stmt(date(2026, 8, 18)).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "content_items.scheduled_date <= '2026-08-18'" in compiled
    assert "content_items.status IN ('DRAFT', 'REJECTED', 'READY')" in compiled
    assert "hospitals.status = 'ACTIVE'" in compiled
    assert "hospitals.site_live IS true" in compiled
    assert "content_schedules.is_active IS true" in compiled
    assert "content_items.carried_over_from IS NOT NULL" in compiled
    assert "LIMIT 100" in compiled


def test_recovery_task_is_registered_routed_and_scheduled_before_generation():
    task_name = "app.workers.content_backlog_recovery.reconcile"
    entry = celery_app.conf.beat_schedule["stranded-content-recovery"]

    assert task_name in celery_app.tasks
    assert celery_app.conf.task_routes[task_name] == {"queue": "default"}
    assert entry["task"] == task_name
    assert entry["schedule"].hour == {22}
    assert entry["schedule"].minute == {30}
    assert REDBEAT_SCHEDULE_VERSION >= "2026-08-18.1"
