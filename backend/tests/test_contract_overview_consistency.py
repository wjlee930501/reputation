"""Admin contract-month facts must agree with the autonomous contract ledger."""

from datetime import date
from unittest.mock import AsyncMock

import arrow
from test_geo_autonomy_hardening import NOW, AsyncDB, seed
from test_geo_autonomy_hardening import db as db

from app.api.admin import hospital_overview as overview
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Plan
from app.services.content_month_target import INITIAL_SCHEDULE_ACTOR


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


async def test_overview_shows_the_prorated_first_contract_month(db, monkeypatch):
    """첫 일정이 월 중간에 시작해 안분된 첫 달은 현황 카드도 그 편수를 약속으로 보인다."""

    hospital, schedule = seed(db)
    schedule.active_from = date(2026, 9, 14)
    for row in db.query(ContentItem).all():
        row.total_count = 5
    db.add(
        AdminAuditLog(
            hospital_id=hospital.id, actor=INITIAL_SCHEDULE_ACTOR, action="set_schedule",
            target_type="content_schedule", target_id=str(schedule.id),
            detail={"active_from": "2026-09-14", "first_month_target": 5},
        )
    )
    db.flush()
    monkeypatch.setattr(overview.arrow, "now", lambda *args: arrow.get(NOW))
    monkeypatch.setattr(overview, "weekly_mention_trend", AsyncMock(return_value=[]))

    result = await overview._month_summary(AsyncDB(db), hospital)

    assert result.planned_total == 5


async def test_overview_promises_zero_for_a_first_month_without_publish_days(db, monkeypatch):
    """PLAN_20 첫 일정이 10/31(토)에 시작해 10월에 게시 요일이 없으면 현황도 0편을 약속으로 보인다."""

    hospital, schedule = seed(db)
    hospital.plan = Plan.PLAN_20
    schedule.plan = "PLAN_20"
    schedule.publish_days = [0, 2, 4]
    schedule.active_from = date(2026, 10, 31)
    db.add(
        AdminAuditLog(
            hospital_id=hospital.id, actor=INITIAL_SCHEDULE_ACTOR, action="set_schedule",
            target_type="content_schedule", target_id=str(schedule.id),
            detail={"active_from": "2026-10-31", "first_month_target": 0},
        )
    )
    db.flush()
    monkeypatch.setattr(overview.arrow, "now", lambda *args: arrow.get(date(2026, 10, 31)))
    monkeypatch.setattr(overview, "weekly_mention_trend", AsyncMock(return_value=[]))

    result = await overview._month_summary(AsyncDB(db), hospital)

    assert result.planned_total == 0
