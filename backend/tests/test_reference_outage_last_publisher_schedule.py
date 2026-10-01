"""기관 사이트 장애 미룸 알림의 '마지막 발행기' 시각이 실제 beat 일정과 맞는지 묶는다.

`reference_outage_alert_due`는 예정일 당일 `now_kst.hour >= REFERENCE_OUTAGE_LAST_PUBLISHER_HOUR`를
'그날의 마지막 발행기'로 읽는다. 상수만 두고 beat 일정을 옮기면(예: 22시에 끝냄, 23:30 추가)
알림이 한 번도 나가지 않거나 두 번 나가므로, 복사본이 아니라 실제 `celery_app` 일정을 읽는다.
"""

from __future__ import annotations

import copy
from datetime import date, datetime
from zoneinfo import ZoneInfo

from celery.beat import ScheduleEntry
from celery.schedules import crontab

from app.core.celery_app import celery_app
from app.services.reference_publication import (
    REFERENCE_OUTAGE_LAST_PUBLISHER_HOUR,
    reference_outage_alert_due,
)

PUBLISHER_TASK = "app.workers.tasks.morning_content_auto_publish"
KST = ZoneInfo("Asia/Seoul")


def _publisher_entries() -> list[tuple[str, crontab]]:
    entries = [
        (name, entry["schedule"])
        for name, entry in (celery_app.conf.beat_schedule or {}).items()
        if entry.get("task") == PUBLISHER_TASK
    ]
    assert entries, "발행기 beat 항목이 없다"
    return entries


def _daily_run_times() -> list[tuple[int, int]]:
    """모든 발행기 항목의 (시, 분) 실행 시각 — 매일 도는 crontab만 허용한다."""

    times: list[tuple[int, int]] = []
    for name, schedule in _publisher_entries():
        assert isinstance(schedule, crontab), (
            f"{name}: crontab이 아니면 하루의 마지막 실행을 정할 수 없다"
        )
        # 규칙은 '매일의 마지막 실행'을 전제한다 — 요일·날짜·월 제한이 있으면 안 된다.
        assert schedule.day_of_week == set(range(7)), name
        assert schedule.day_of_month == set(range(1, 32)), name
        assert schedule.month_of_year == set(range(1, 13)), name
        times.extend((hour, minute) for hour in schedule.hour for minute in schedule.minute)
    assert len(times) == len(set(times)), f"같은 시각에 발행기가 두 번 돈다: {sorted(times)}"
    return sorted(times)


def test_beat_hours_are_read_in_kst() -> None:
    """규칙은 KST 시각을 비교한다 — beat가 crontab 시각을 Asia/Seoul로 해석해야 한다."""

    assert celery_app.conf.timezone == "Asia/Seoul"
    for name, schedule in _publisher_entries():
        # 별도 시계(nowfun)가 있으면 앱 시간대를 따르지 않는다.
        assert schedule.nowfun is None, name
        # beat(RedBeat 포함)가 하듯 앱에 묶은 사본의 시간대를 본다(원본의 cached tz는 건드리지 않는다).
        bound = ScheduleEntry(
            name=name, task=PUBLISHER_TASK, schedule=copy.copy(schedule), app=celery_app
        )
        assert getattr(bound.schedule.tz, "key", str(bound.schedule.tz)) == "Asia/Seoul", name


def test_the_last_publisher_hour_is_the_last_beat_run_of_the_day() -> None:
    """상수 = 발행기의 마지막 시(時)이고, 그 시각 이후 실행은 하루 한 번(그날의 마지막)뿐이다."""

    times = _daily_run_times()

    assert max(hour for hour, _minute in times) == REFERENCE_OUTAGE_LAST_PUBLISHER_HOUR
    at_or_after = [
        (hour, minute) for hour, minute in times if hour >= REFERENCE_OUTAGE_LAST_PUBLISHER_HOUR
    ]
    assert at_or_after == [times[-1]], (
        f"마지막 발행기 시각 이후 실행이 하나가 아니다: {at_or_after}"
    )


def test_alert_is_due_on_exactly_the_last_real_run_of_the_scheduled_date() -> None:
    """실제 실행 시각마다 `reference_outage_alert_due`를 부르면 예정일에는 마지막 실행 한 번만 참이다."""

    scheduled = date(2026, 9, 30)
    times = _daily_run_times()
    due = [
        (hour, minute)
        for hour, minute in times
        if reference_outage_alert_due(scheduled, datetime(2026, 9, 30, hour, minute, tzinfo=KST))
    ]

    assert due == [times[-1]]
