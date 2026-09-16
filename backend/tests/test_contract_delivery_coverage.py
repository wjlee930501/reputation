"""Missing calendars and carryovers must not disappear from the daily facts."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, select
from test_geo_autonomy_hardening import NOW, heartbeat, seed
from test_geo_autonomy_hardening import db as db

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import HospitalStatus
from app.services.contract_delivery_coverage import collect_contract_delivery_coverage
from app.services.fleet_heartbeat import FleetFacts


def active(db):
    hospital, schedule = seed(db)
    hospital.site_live = True
    db.flush()
    return hospital, schedule


def item(db, hospital, schedule, number=6, **values):
    row = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.HEALTH,
        sequence_no=number,
        total_count=12,
        scheduled_date=date(2026, 9, 28),
        status=ContentStatus.DRAFT,
    )
    for key, value in values.items():
        setattr(row, key, value)
    db.add(row)
    db.flush()
    return row


def test_completely_missing_calendar_still_has_a_contract_denominator(db):
    active(db)
    db.execute(delete(ContentItem))
    facts = collect_contract_delivery_coverage(db, now=NOW)
    assert (facts.expected, facts.allocated, facts.missing_allocations) == (12, 0, 12)
    assert facts.delivery_at_risk


def test_existing_five_rows_do_not_redefine_the_purchase_as_five(db):
    active(db)
    facts = collect_contract_delivery_coverage(db, now=NOW)
    assert (facts.expected, facts.allocated, facts.first_published) == (12, 5, 5)
    assert facts.missing_allocations == 7


def test_cancelled_work_is_not_treated_as_fulfillable_or_automatically_recreated(db):
    hospital, schedule = active(db)
    for number in range(6, 13):
        item(
            db,
            hospital,
            schedule,
            number,
            status=ContentStatus.CANCELLED if number == 12 else ContentStatus.DRAFT,
        )
    facts = collect_contract_delivery_coverage(db, now=NOW)
    assert facts.missing_allocations == 0 and facts.cancelled_deficit == 1
    assert len(list(db.scalars(select(ContentItem)))) == 12
    item(db, hospital, schedule, 13)
    assert collect_contract_delivery_coverage(db, now=NOW).cancelled_deficit == 0


def test_incoming_carryover_does_not_fill_this_months_quota(db):
    hospital, schedule = active(db)
    item(
        db,
        hospital,
        schedule,
        carried_over_from=date(2026, 8, 28),
        first_published_at=NOW,
        published_at=NOW,
        status=ContentStatus.PUBLISHED,
    )
    facts = collect_contract_delivery_coverage(db, now=NOW)
    assert (facts.allocated, facts.first_published, facts.missing_allocations) == (5, 5, 7)


def test_outgoing_carryover_retains_original_obligation_and_is_not_completed(db):
    hospital, schedule = active(db)
    item(
        db, hospital, schedule, carried_over_from=date(2026, 9, 2), scheduled_date=date(2026, 10, 2)
    )
    facts = collect_contract_delivery_coverage(db, now=NOW)
    assert facts.allocated == 6 and facts.first_published == 5
    assert facts.carried_out_unpublished == 1


def test_first_publication_survives_withdrawal_and_a_later_publication_timestamp(db):
    active(db)
    row = db.scalars(select(ContentItem)).first()
    row.status = ContentStatus.REJECTED
    row.published_at = datetime(2026, 10, 1, tzinfo=UTC)
    db.flush()
    facts = collect_contract_delivery_coverage(db, now=NOW)
    assert facts.first_published == 5 and facts.overdue_unpublished == 0


def test_future_effective_plan_cannot_change_current_month_denominator(db):
    hospital, old = active(db)
    old.is_active = False
    db.add(
        ContentSchedule(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            plan="PLAN_20",
            publish_days=[0, 2, 4],
            active_from=date(2026, 10, 1),
            is_active=True,
        )
    )
    db.flush()
    assert collect_contract_delivery_coverage(db, now=NOW).expected == 12


def test_disabled_schedule_is_unknown_not_a_success(db):
    _, old = active(db)
    old.is_active = False
    db.flush()
    facts = collect_contract_delivery_coverage(db, now=NOW)
    assert facts.unknown_schedule_hospitals == 1 and facts.expected == 0


@pytest.mark.parametrize("hour,expected", [(7, 0), (8, 1), (18, 1)])
def test_today_is_not_overdue_before_the_first_publish_window(db, hour, expected):
    hospital, schedule = active(db)
    item(db, hospital, schedule, scheduled_date=date(2026, 9, 16))
    observed = datetime(2026, 9, 16, hour - 9 if hour >= 9 else hour + 15, tzinfo=UTC)
    if hour < 9:
        observed = observed.replace(day=15)
    assert collect_contract_delivery_coverage(db, now=observed).overdue_unpublished == expected


def test_paused_hospitals_are_not_currently_operational_coverage(db):
    hospital, _ = active(db)
    hospital.status = HospitalStatus.PAUSED
    db.flush()
    facts = collect_contract_delivery_coverage(db, now=NOW)
    assert facts.expected == facts.allocated == facts.unknown_schedule_hospitals == 0


def test_extra_rows_in_one_hospital_cannot_offset_another_hospitals_shortfall(db):
    first, schedule = active(db)
    for number in range(6, 25):
        item(db, first, schedule, number)
    active(db)
    facts = collect_contract_delivery_coverage(db, now=NOW)
    assert facts.expected == 24 and facts.allocated == 29
    assert facts.missing_allocations == 7


def test_heartbeat_no_longer_calls_a_missing_calendar_healthy(db):
    active(db)
    coverage = collect_contract_delivery_coverage(db, now=NOW)
    facts = FleetFacts(1, 0, 0, 0, 1, 1, 0, coverage)
    text = heartbeat(facts=facts).message.fallback_text
    assert "미완료 항목 있음" in text.splitlines()[0]
    assert "확인된 약정 12건" in text and "미배정 7건" in text
    assert "최초 발행은 현재 공개 건수와 다르며" in text


def test_explicit_future_first_start_is_not_a_missing_current_schedule(db):
    _, schedule = active(db)
    db.execute(delete(ContentItem))
    schedule.active_from = date(2026, 10, 1)
    db.flush()
    facts = collect_contract_delivery_coverage(db, now=NOW)
    assert facts.expected == facts.allocated == facts.unknown_schedule_hospitals == 0


def test_coverage_read_does_not_mutate_existing_rows_or_commit(db):
    active(db)
    before = [(row.id, row.status, row.scheduled_date) for row in db.scalars(select(ContentItem))]
    transaction = db.get_transaction()
    collect_contract_delivery_coverage(db, now=NOW)
    assert db.get_transaction() is transaction
    assert not db.dirty and not db.new
    assert before == [
        (row.id, row.status, row.scheduled_date) for row in db.scalars(select(ContentItem))
    ]
