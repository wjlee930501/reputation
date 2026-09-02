"""Month-close boundary and historical service eligibility contracts."""

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.celery_app import celery_app
from app.services.monthly_manifest import _month_close
from app.services.monthly_period import (
    MonthlyPeriodError,
    ReportBuildReason,
    plan_report_version,
    prior_month_to_close,
    reporting_period,
    require_closed_period,
    scheduled_report_period,
    service_interval_overlaps,
)

KST = ZoneInfo("Asia/Seoul")


def test_pin_manifest_close_is_first_day_at_0015_kst() -> None:
    """PIN: manifests already close at 00:15 KST on the next month's first day."""

    assert _month_close(2026, 12) == datetime(2027, 1, 1, 0, 15, tzinfo=KST)
    assert _month_close(2028, 2) == datetime(2028, 3, 1, 0, 15, tzinfo=KST)


def test_monthly_close_runs_once_on_the_first_day_kst() -> None:
    schedule = celery_app.conf.beat_schedule["monthly-reports"]["schedule"]

    assert schedule.minute == {15}
    assert schedule.hour == {0}
    assert schedule.day_of_month == {1}


def test_reporting_period_handles_leap_year_and_year_boundary() -> None:
    leap_february = reporting_period(2028, 2)
    december = reporting_period(2026, 12)

    assert leap_february.starts_at == datetime(2028, 2, 1, tzinfo=KST)
    assert leap_february.ends_at == datetime(2028, 3, 1, tzinfo=KST)
    assert leap_february.closes_at == datetime(2028, 3, 1, 0, 15, tzinfo=KST)
    assert december.ends_at == datetime(2027, 1, 1, tzinfo=KST)


def test_prior_month_closes_only_at_or_after_0015_kst() -> None:
    exact_cutoff = prior_month_to_close(datetime(2026, 9, 1, 0, 15, tzinfo=KST))
    august_cutoff = prior_month_to_close(datetime(2026, 8, 1, 0, 15, tzinfo=KST))

    assert (exact_cutoff.year, exact_cutoff.month) == (2026, 8)
    assert (august_cutoff.year, august_cutoff.month) == (2026, 7)
    with pytest.raises(MonthlyPeriodError, match="00시 15분 이후"):
        prior_month_to_close(datetime(2026, 9, 1, 0, 14, 59, tzinfo=KST))


def test_manual_period_rejects_current_and_future_with_next_action() -> None:
    now = datetime(2026, 9, 1, 0, 15, tzinfo=KST)

    assert require_closed_period(2026, 8, now=now).month == 8
    with pytest.raises(MonthlyPeriodError, match="지난달까지만 선택"):
        require_closed_period(2026, 9, now=now)
    with pytest.raises(MonthlyPeriodError, match="지난달까지만 선택"):
        require_closed_period(2027, 1, now=now)


def test_august_2026_conversion_period_is_allowed_on_august_31() -> None:
    period = require_closed_period(
        2026, 8, now=datetime(2026, 8, 31, 12, tzinfo=KST)
    )

    assert (period.year, period.month) == (2026, 8)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 8, 31, 12, tzinfo=KST),
        datetime(2026, 9, 1, 0, 14, tzinfo=KST),
    ],
)
def test_september_2026_still_requires_its_normal_close(now: datetime) -> None:
    with pytest.raises(MonthlyPeriodError, match="지난달까지만 선택"):
        require_closed_period(2026, 9, now=now)


def test_scheduled_report_period_uses_august_only_inside_conversion_window() -> None:
    august_today = scheduled_report_period(datetime(2026, 8, 31, 12, tzinfo=KST))
    july_on_first = scheduled_report_period(datetime(2026, 8, 1, 0, 15, tzinfo=KST))
    august_on_september_close = scheduled_report_period(
        datetime(2026, 9, 1, 0, 15, tzinfo=KST)
    )

    assert (august_today.year, august_today.month) == (2026, 8)
    assert (july_on_first.year, july_on_first.month) == (2026, 7)
    assert (august_on_september_close.year, august_on_september_close.month) == (2026, 8)


@pytest.mark.parametrize(
    ("started_at", "ended_at", "eligible"),
    [
        (datetime(2026, 8, 10, tzinfo=KST), None, True),
        (datetime(2026, 7, 1, tzinfo=KST), datetime(2026, 8, 15, tzinfo=KST), True),
        (datetime(2026, 7, 1, tzinfo=KST), datetime(2026, 8, 1, tzinfo=KST), False),
        (datetime(2026, 9, 1, tzinfo=KST), None, False),
        (
            datetime(2026, 7, 31, 15, tzinfo=UTC),
            datetime(2026, 8, 1, 15, tzinfo=UTC),
            True,
        ),
    ],
)
def test_service_eligibility_uses_effective_interval_overlap(
    started_at: datetime, ended_at: datetime | None, eligible: bool
) -> None:
    assert (
        service_interval_overlaps(
            reporting_period(2026, 8), started_at=started_at, ended_at=ended_at
        )
        is eligible
    )


def test_report_versions_preserve_v1_and_require_explicit_reason_for_v2() -> None:
    initial = plan_report_version(
        latest_report_id=None,
        latest_version=None,
        reason_code=ReportBuildReason.SCHEDULED_CLOSE,
        correlation_key="scheduled:hospital:2026-08",
    )
    prior_id = uuid.uuid4()
    replay = plan_report_version(
        latest_report_id=prior_id,
        latest_version=1,
        reason_code=ReportBuildReason.SCHEDULED_CLOSE,
        correlation_key="scheduled:hospital:2026-08",
    )
    late_data = plan_report_version(
        latest_report_id=prior_id,
        latest_version=1,
        reason_code=ReportBuildReason.LATE_DATA_REBUILD,
        correlation_key="operation-run:7d744a2e",
    )

    assert (initial.version, initial.supersedes_report_id, initial.create) == (1, None, True)
    assert (replay.version, replay.supersedes_report_id, replay.create) == (1, None, False)
    assert (late_data.version, late_data.supersedes_report_id, late_data.create) == (
        2,
        prior_id,
        True,
    )
    assert late_data.reason_code is ReportBuildReason.LATE_DATA_REBUILD
    assert late_data.correlation_key == "operation-run:7d744a2e"


def test_rebuild_cannot_create_an_unlinked_first_version() -> None:
    with pytest.raises(MonthlyPeriodError, match="기존 리포트가 없습니다"):
        plan_report_version(
            latest_report_id=None,
            latest_version=None,
            reason_code=ReportBuildReason.LATE_DATA_REBUILD,
            correlation_key="operation-run:missing-parent",
        )
