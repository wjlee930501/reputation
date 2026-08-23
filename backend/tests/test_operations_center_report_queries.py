from datetime import UTC, datetime, timedelta

import pytest

from app.api.admin import operations_center_report_queries as report_queries
from app.api.admin.operations_center_query_common import OperationsFilters, SlaFilter
from app.api.admin.operations_center_serializers import sla_state


class _EmptyResult:
    def all(self):
        return []


class _RecordingDb:
    def __init__(self):
        self.execute_calls = 0

    async def execute(self, _statement):
        self.execute_calls += 1
        return _EmptyResult()

    async def scalar(self, _statement):
        return 0


def test_previous_period_exposes_the_fifteen_minute_monthly_close():
    now = datetime(2026, 7, 31, 15, 5, tzinfo=UTC)

    year, month, _starts_at, ends_at, closes_at = report_queries._previous_period(now)

    assert (year, month) == (2026, 7)
    assert closes_at == ends_at + timedelta(minutes=15)
    assert sla_state(closes_at, now) == "DUE"
    assert sla_state(closes_at, closes_at + timedelta(minutes=1)) == "OVERDUE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("now", "accepted", "rejected"),
    [
        (datetime(2026, 7, 31, 15, 5, tzinfo=UTC), SlaFilter.DUE, SlaFilter.OVERDUE),
        (datetime(2026, 7, 31, 15, 16, tzinfo=UTC), SlaFilter.OVERDUE, SlaFilter.DUE),
    ],
)
async def test_report_sla_filter_matches_the_close_time_state(now, accepted, rejected):
    accepted_db = _RecordingDb()
    await report_queries.load_reports_queue(
        accepted_db,
        OperationsFilters(sla=accepted),
        page=1,
        page_size=10,
        overview=False,
        now=now,
    )
    assert accepted_db.execute_calls == 1

    rejected_db = _RecordingDb()
    result = await report_queries.load_reports_queue(
        rejected_db,
        OperationsFilters(sla=rejected),
        page=1,
        page_size=10,
        overview=False,
        now=now,
    )
    assert result == (0, [])
    assert rejected_db.execute_calls == 0
