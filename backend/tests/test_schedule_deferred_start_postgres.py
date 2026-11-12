"""첫 발행 전 시작일을 뒤 달로 미루면 앞 달 슬롯이 종료되고 그 달 약속이 0편이 된다.

2026-11-12 365서귀포삼성정형외과: 11/13 시작(월·수·금)을 12/1로 옮겼는데 11월 슬롯 8개가
남아 자동 생성·발행 대상이었다.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import arrow
import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.admin import content as content_api
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.services.content_month_target import month_content_target
from tests.db_env import require_db_url

NOV = arrow.get("2026-11-01")
DEC = arrow.get("2026-12-01")


def _engine():
    url = make_url(require_db_url("DATABASE_URL"))
    assert (url.database or "").endswith("_test"), "Only a *_test database may be used"
    return create_async_engine(url)


async def _seed(db, *, published=False, human_edited=False):
    hospital = Hospital(
        id=uuid.uuid4(),
        name="시작일 미루기",
        slug="defer-" + uuid.uuid4().hex[:16],
        status=HospitalStatus.ACTIVE,
        site_live=True,
        plan=Plan.PLAN_12,
        schedule_set=True,
    )
    db.add(hospital)
    await db.flush()
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[0, 2, 4],
        active_from=date(2026, 11, 13),
        is_active=True,
    )
    db.add(schedule)
    await db.flush()
    db.add(
        AdminAuditLog(
            hospital_id=hospital.id,
            actor="system:essence-auto-approval",
            action="set_schedule",
            target_type="content_schedule",
            target_id=str(schedule.id),
            detail={"active_from": "2026-11-13", "first_month_target": 8},
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    days = [13, 16, 18, 20, 23, 25, 27, 30]
    items = []
    for seq, day in enumerate(days, start=1):
        item = ContentItem(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            schedule_id=schedule.id,
            content_type=ContentType.HEALTH,
            sequence_no=seq,
            total_count=8,
            scheduled_date=date(2026, 11, day),
            status=ContentStatus.DRAFT,
        )
        items.append(item)
    if published:
        items[0].status = ContentStatus.PUBLISHED
        items[0].first_published_at = datetime(2026, 11, 13, 0, tzinfo=timezone.utc)
        items[0].published_at = items[0].first_published_at
    if human_edited:
        items[1].human_edited_at = datetime.now(timezone.utc)
    db.add_all(items)
    await db.commit()
    return hospital, schedule, items


@pytest.fixture
def frozen_today(monkeypatch):
    real_now = arrow.now

    def fake_now(tz=None):
        return real_now(tz).replace(year=2026, month=11, day=12, hour=14)

    monkeypatch.setattr(content_api.arrow, "now", fake_now)

    async def ok(*_args, **_kwargs):
        return []

    monkeypatch.setattr(content_api, "_schedule_readiness_blockers", ok)
    monkeypatch.setattr(
        content_api.regenerate_content_item, "apply_async", lambda *a, **k: None
    )


async def _statuses(db, hospital_id):
    rows = (
        await db.execute(
            select(ContentItem.scheduled_date, ContentItem.status)
            .where(ContentItem.hospital_id == hospital_id)
            .order_by(ContentItem.scheduled_date)
        )
    ).all()
    return rows


async def _cleanup(engine, hospital_id):
    async with AsyncSession(engine) as db:
        await db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        await db.commit()


async def test_moving_an_unstarted_start_to_next_month_cancels_old_slots(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital, _schedule, _items = await _seed(db)
            hospital_id = hospital.id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            body = content_api.ScheduleCreate(
                plan="PLAN_12", publish_days=[0, 2, 4], active_from=date(2026, 12, 1)
            )
            response = await content_api.set_schedule(hospital_id, body, db=db)
        assert response["slots_cancelled_before_start"] == 8
        assert response["slots_created"] == 12

        async with AsyncSession(engine, expire_on_commit=False) as db:
            rows = await _statuses(db, hospital_id)
            november = [status for day, status in rows if day.month == 11]
            december = [status for day, status in rows if day.month == 12]
            assert november == [ContentStatus.CANCELLED] * 8
            assert len(december) == 12 and set(december) == {ContentStatus.DRAFT}
            # 11월은 약속 0편, 12월은 요금제 편수 그대로.
            assert await month_content_target(db, hospital_id, NOV, "PLAN_12") == 0
            assert await month_content_target(db, hospital_id, DEC, "PLAN_12") == 12

        # 다시 11월 시작으로 저장하면 11월은 다시 약속 달이고 새 슬롯이 생긴다(0편 기록이 풀린다).
        async with AsyncSession(engine, expire_on_commit=False) as db:
            body = content_api.ScheduleCreate(
                plan="PLAN_12", publish_days=[0, 2, 4], active_from=date(2026, 11, 13)
            )
            reopened = await content_api.set_schedule(hospital_id, body, db=db)
        assert reopened["slots_created"] == 8
        async with AsyncSession(engine, expire_on_commit=False) as db:
            target = await month_content_target(db, hospital_id, NOV, "PLAN_12")
            assert target == 8
            rows = await _statuses(db, hospital_id)
            live = [d for d, s in rows if d.month == 11 and s != ContentStatus.CANCELLED]
            assert len(live) == reopened["slots_created"]
            assert min(live) >= date(2026, 11, 13)
    finally:
        if hospital_id is not None:
            await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_two_month_deferral_voids_the_month_in_between(frozen_today):
    """11/13 → 2027-01-01이면 슬롯이 없던 12월도 0편이다 — 당월 조정이 옛 일정으로 12월을 채우지 않는다."""
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital, _schedule, _items = await _seed(db)
            hospital_id = hospital.id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            body = content_api.ScheduleCreate(
                plan="PLAN_12", publish_days=[0, 2, 4], active_from=date(2027, 1, 1)
            )
            await content_api.set_schedule(hospital_id, body, db=db)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert await month_content_target(db, hospital_id, NOV, "PLAN_12") == 0
            assert await month_content_target(db, hospital_id, DEC, "PLAN_12") == 0
            assert (
                await month_content_target(db, hospital_id, arrow.get("2027-01-01"), "PLAN_12")
                == 12
            )
    finally:
        if hospital_id is not None:
            await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_resaving_the_already_moved_start_cleans_leftover_slots(frozen_today):
    """운영 상황 그대로 — 시작일을 이미 12/1로 옮겨 둔 뒤 같은 일정을 다시 저장해도 11월이 정리된다."""
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital, schedule, _items = await _seed(db)
            hospital_id = hospital.id
            schedule.is_active = False
            db.add(
                ContentSchedule(
                    hospital_id=hospital_id,
                    plan="PLAN_12",
                    publish_days=[0, 2, 4],
                    active_from=date(2026, 12, 1),
                    is_active=True,
                )
            )
            await db.commit()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            body = content_api.ScheduleCreate(
                plan="PLAN_12", publish_days=[0, 2, 4], active_from=date(2026, 12, 1)
            )
            response = await content_api.set_schedule(hospital_id, body, db=db)
        assert response["slots_cancelled_before_start"] == 8
        async with AsyncSession(engine, expire_on_commit=False) as db:
            rows = await _statuses(db, hospital_id)
            assert [s for d, s in rows if d.month == 11] == [ContentStatus.CANCELLED] * 8
            assert await month_content_target(db, hospital_id, NOV, "PLAN_12") == 0
    finally:
        if hospital_id is not None:
            await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_started_schedule_keeps_its_slots(monkeypatch, frozen_today):
    """이미 시작한 일정(시작일 ≤ 오늘)의 늦은 시작일은 '그날부터 새 조건'이다 — 종료하지 않는다."""
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital, schedule, _items = await _seed(db)
            hospital_id = hospital.id
            schedule.active_from = date(2026, 11, 2)
            await db.commit()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            body = content_api.ScheduleCreate(
                plan="PLAN_12", publish_days=[0, 2, 4], active_from=date(2026, 12, 1)
            )
            response = await content_api.set_schedule(hospital_id, body, db=db)
        assert response["slots_cancelled_before_start"] == 0
    finally:
        if hospital_id is not None:
            await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_hospital_with_a_publication_keeps_its_slots(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital, _schedule, _items = await _seed(db, published=True)
            hospital_id = hospital.id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            body = content_api.ScheduleCreate(
                plan="PLAN_12", publish_days=[0, 2, 4], active_from=date(2026, 12, 1)
            )
            response = await content_api.set_schedule(hospital_id, body, db=db)
        assert response["slots_cancelled_before_start"] == 0
        async with AsyncSession(engine, expire_on_commit=False) as db:
            rows = await _statuses(db, hospital_id)
            assert ContentStatus.CANCELLED not in {status for _day, status in rows}
    finally:
        if hospital_id is not None:
            await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_human_edited_slot_is_kept_and_its_month_stays_promised(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital, _schedule, items = await _seed(db, human_edited=True)
            hospital_id = hospital.id
            edited_id = items[1].id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            body = content_api.ScheduleCreate(
                plan="PLAN_12", publish_days=[0, 2, 4], active_from=date(2026, 12, 1)
            )
            response = await content_api.set_schedule(hospital_id, body, db=db)
        assert response["slots_cancelled_before_start"] == 7
        assert response["slots_kept_before_start"] == 1
        async with AsyncSession(engine, expire_on_commit=False) as db:
            edited = await db.get(ContentItem, edited_id)
            assert edited.status == ContentStatus.DRAFT
            # 미룬 달의 약속은 살아 있는 슬롯 수(사람이 고쳐 남긴 원고 1편)다.
            assert await month_content_target(db, hospital_id, NOV, "PLAN_12") == 1
    finally:
        if hospital_id is not None:
            await _cleanup(engine, hospital_id)
        await engine.dispose()
