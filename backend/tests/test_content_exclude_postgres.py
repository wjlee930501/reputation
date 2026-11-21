"""발행 제외 — 이 글은 종료하고 같은 달 다른 날에 새 칸을 만든다(개편 결정 대기 6번 A).

그냥 종료하면 그 달 약속 편수가 모자라 계약 이행·일일 요약이 미달로 본다.
"""

import uuid
from datetime import date, datetime, timezone

import arrow
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.admin import content as content_api
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.services.content_month_target import month_allocation_count_stmt
from tests.db_env import require_db_url

NOV = arrow.get("2026-11-01")


def _engine():
    url = make_url(require_db_url("DATABASE_URL"))
    assert (url.database or "").endswith("_test"), "Only a *_test database may be used"
    return create_async_engine(url)


@pytest.fixture
def frozen_today(monkeypatch):
    real_now = arrow.now

    def fake_now(tz=None):
        return real_now(tz).replace(year=2026, month=11, day=12, hour=14)

    monkeypatch.setattr(content_api.arrow, "now", fake_now)


async def _seed(db, *, daily_max=1, days=(13, 16, 18, 20, 23, 25, 27, 30)):
    hospital = Hospital(
        id=uuid.uuid4(),
        name="발행 제외",
        slug="exclude-" + uuid.uuid4().hex[:12],
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
        daily_max=daily_max,
        is_active=True,
    )
    db.add(schedule)
    await db.flush()
    items = []
    for seq, day in enumerate(days, start=1):
        item = ContentItem(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            schedule_id=schedule.id,
            content_type=ContentType.HEALTH,
            sequence_no=seq,
            total_count=len(days),
            scheduled_date=date(2026, 11, day),
            status=ContentStatus.DRAFT,
            title=f"원고 {seq}",
        )
        db.add(item)
        items.append(item)
    await db.commit()
    return hospital, items


async def _cleanup(engine, hospital_id):
    if hospital_id is None:
        return
    async with AsyncSession(engine) as db:
        await db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        await db.commit()


async def test_excluding_cancels_the_slot_and_keeps_the_month_count(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital, items = await _seed(db, daily_max=2)
            hospital_id = hospital.id
            target = items[1]  # 11/16
        async with AsyncSession(engine, expire_on_commit=False) as db:
            response = await content_api.exclude_content(
                hospital_id, target.id, content_api.ExcludeBody(reason="주제가 겹침"), db=db
            )
        # 내일(11/13) 이후 발행 요일 중 자리가 남은 가장 이른 날(같은 날은 쓰지 않는다).
        assert response["scheduled_date"] == "2026-11-13"
        assert response["title"] is None
        async with AsyncSession(engine, expire_on_commit=False) as db:
            original = await db.get(ContentItem, target.id)
            assert original.status == ContentStatus.CANCELLED
            assert (await db.execute(month_allocation_count_stmt(hospital_id, NOV))).scalar_one() == 8
            audit = (
                await db.execute(
                    select(AdminAuditLog.detail).where(
                        AdminAuditLog.hospital_id == hospital_id,
                        AdminAuditLog.action == "exclude_content",
                    )
                )
            ).scalar_one()
            assert audit["replacement_date"] == "2026-11-13"
            assert audit["reason"] == "주제가 겹침"
    finally:
        await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_same_date_is_not_reused_so_a_full_month_is_refused(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            # 11/13 이후 월·수·금 8일이 하루 1편으로 모두 찼다 — 빠지는 글의 자리만 빈다.
            hospital, items = await _seed(db, daily_max=1)
            hospital_id = hospital.id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            with pytest.raises(HTTPException) as exc:
                await content_api.exclude_content(hospital_id, items[2].id, None, db=db)
        assert exc.value.detail["code"] == "EXCLUDE_MONTH_FULL"
    finally:
        await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_full_month_is_refused_and_nothing_changes(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            # 오늘(11/12, 목) 글을 빼려는데 내일 이후 발행 요일이 하루 1편으로 모두 찼다.
            hospital, items = await _seed(db, daily_max=1, days=(12, 13, 16, 18, 20, 23, 25, 27, 30))
            hospital_id = hospital.id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            with pytest.raises(HTTPException) as exc:
                await content_api.exclude_content(hospital_id, items[0].id, None, db=db)
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "EXCLUDE_MONTH_FULL"
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert (await db.get(ContentItem, items[0].id)).status == ContentStatus.DRAFT
    finally:
        await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_published_or_generating_content_cannot_be_excluded(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital, items = await _seed(db)
            hospital_id = hospital.id
            items[0].first_published_at = datetime(2026, 11, 1, tzinfo=timezone.utc)
            items[1].generation_claim_token = uuid.uuid4()
            await db.commit()
        for item, code in ((items[0], "EXCLUDE_NOT_ALLOWED"), (items[1], "EXCLUDE_GENERATING")):
            async with AsyncSession(engine, expire_on_commit=False) as db:
                with pytest.raises(HTTPException) as exc:
                    await content_api.exclude_content(hospital_id, item.id, None, db=db)
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == code
        async with AsyncSession(engine, expire_on_commit=False) as db:
            live = (
                await db.execute(
                    select(func.count(ContentItem.id)).where(
                        ContentItem.hospital_id == hospital_id,
                        ContentItem.status != ContentStatus.CANCELLED,
                    )
                )
            ).scalar_one()
        assert live == 8
    finally:
        await _cleanup(engine, hospital_id)
        await engine.dispose()
