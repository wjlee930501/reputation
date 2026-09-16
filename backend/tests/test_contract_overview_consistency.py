"""Admin contract-month facts must agree with the autonomous contract ledger."""

from datetime import date
from unittest.mock import AsyncMock

import arrow
from test_geo_autonomy_hardening import NOW, AsyncDB, seed
from test_geo_autonomy_hardening import db as db

from app.api.admin import hospital_overview as overview
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Plan


async def test_overview_uses_current_terms_not_the_future_plan(db, monkeypatch):
    hospital, old = seed(db)
    old.is_active = False
    hospital.plan = Plan.PLAN_20
    db.add(
        ContentSchedule(
            hospital_id=hospital.id,
            plan="PLAN_20",
            publish_days=[0, 2, 4],
            active_from=date(2026, 10, 1),
            is_active=True,
        )
    )
    db.flush()
    monkeypatch.setattr(overview.arrow, "now", lambda *args: arrow.get(NOW))
    monkeypatch.setattr(overview, "weekly_mention_trend", AsyncMock(return_value=[]))
    result = await overview._month_summary(AsyncDB(db), hospital)
    assert result.planned_total == 12


async def test_overview_counts_original_month_not_the_recovery_date(db, monkeypatch):
    hospital, schedule = seed(db)
    incoming = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.HEALTH,
        sequence_no=6,
        total_count=12,
        scheduled_date=date(2026, 9, 10),
        carried_over_from=date(2026, 8, 10),
        status=ContentStatus.PUBLISHED,
        first_published_at=NOW,
        published_at=NOW,
    )
    outgoing = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.HEALTH,
        sequence_no=7,
        total_count=12,
        scheduled_date=date(2026, 10, 1),
        carried_over_from=date(2026, 9, 10),
        status=ContentStatus.PUBLISHED,
        first_published_at=NOW,
        published_at=NOW,
    )
    db.add_all([incoming, outgoing])
    db.flush()
    monkeypatch.setattr(overview.arrow, "now", lambda *args: arrow.get(NOW))
    monkeypatch.setattr(overview, "weekly_mention_trend", AsyncMock(return_value=[]))
    result = await overview._month_summary(AsyncDB(db), hospital)
    assert result.published_count == 6
    # Verify which identity counted, not merely equal sums of inbound/outbound rows.
    incoming.status = ContentStatus.DRAFT
    db.flush()
    again = await overview._month_summary(AsyncDB(db), hospital)
    assert again.published_count == 6
