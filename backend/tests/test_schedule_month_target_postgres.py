"""마케터가 계약 첫 달 편수를 정한다(2026-11-21 마케팅 요청 #8).

일정 저장(`POST …/schedule`)의 `month_target`이 그 달 약속 편수가 되고, 계약 이행 집계·현황·보고서·
당월 조정이 같은 판정(`month_content_target`)으로 읽는다. 요금제 편수보다 크게는 못 정하고, 줄이면
손대지 않은 슬롯만 종료한다.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import arrow
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session

from app.api.admin import content as content_api
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.services.content_month_target import (
    CONTRACT_QUOTA_MONTH_KEY,
    OPERATOR_MONTH_TARGET_KEY,
    month_content_target,
    month_content_target_sync,
)
from tests.db_env import require_db_url

NOV = arrow.get("2026-11-01")


def _engine():
    url = make_url(require_db_url("DATABASE_URL"))
    assert (url.database or "").endswith("_test"), "Only a *_test database may be used"
    return create_async_engine(url)


async def _seed(db, *, human_edited=0, generated=0, published_october=False):
    hospital = Hospital(
        id=uuid.uuid4(),
        name="첫 달 편수",
        slug="first-month-" + uuid.uuid4().hex[:12],
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
        if seq <= human_edited:
            item.human_edited_at = datetime.now(timezone.utc)
        if seq <= generated:
            item.title = f"원고 {seq}"
            item.body = "본문"
        db.add(item)
    if published_october:
        db.add(
            ContentItem(
                id=uuid.uuid4(),
                hospital_id=hospital.id,
                schedule_id=schedule.id,
                content_type=ContentType.HEALTH,
                sequence_no=99,
                total_count=12,
                scheduled_date=date(2026, 10, 30),
                status=ContentStatus.PUBLISHED,
                first_published_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
                published_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
            )
        )
    await db.commit()
    return hospital


@pytest.fixture
def frozen_today(monkeypatch):
    real_now = arrow.now

    def fake_now(tz=None):
        return real_now(tz).replace(year=2026, month=11, day=12, hour=14)

    monkeypatch.setattr(content_api.arrow, "now", fake_now)

    async def ok(*_args, **_kwargs):
        return []

    monkeypatch.setattr(content_api, "_schedule_readiness_blockers", ok)
    monkeypatch.setattr(content_api.regenerate_content_item, "apply_async", lambda *a, **k: None)


def _body(month_target=None, daily_max=1):
    return content_api.ScheduleCreate(
        plan="PLAN_12",
        publish_days=[0, 2, 4],
        active_from=date(2026, 11, 13),
        month_target=month_target,
        daily_max=daily_max,
    )


async def _live(db, hospital_id):
    contract_date = func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
    rows = (
        await db.execute(
            select(ContentItem.sequence_no, ContentItem.title)
            .where(
                ContentItem.hospital_id == hospital_id,
                ContentItem.status != ContentStatus.CANCELLED,
                contract_date >= date(2026, 11, 1),
                contract_date <= date(2026, 11, 30),
            )
            .order_by(ContentItem.sequence_no)
        )
    ).all()
    return rows


async def _last_audit(db, hospital_id):
    return (
        await db.execute(
            select(AdminAuditLog.detail)
            .where(AdminAuditLog.hospital_id == hospital_id, AdminAuditLog.action == "set_schedule")
            .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
            .limit(1)
        )
    ).scalar_one()


async def _cleanup(engine, hospital_id):
    if hospital_id is None:
        return
    async with AsyncSession(engine) as db:
        await db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        await db.commit()


async def test_lowering_cancels_untouched_slots_and_every_reader_sees_the_new_target(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital_id = (await _seed(db, generated=6)).id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            response = await content_api.set_schedule(hospital_id, _body(5), db=db)
        assert response["month_target"] == 5
        async with AsyncSession(engine, expire_on_commit=False) as db:
            live = await _live(db, hospital_id)
            # 빈 슬롯(7·8) 먼저, 그다음 원고가 있는 뒤 순번(6)부터 종료했다.
            assert [row.sequence_no for row in live] == [1, 2, 3, 4, 5]
            detail = await _last_audit(db, hospital_id)
            assert detail[OPERATOR_MONTH_TARGET_KEY]["target"] == 5
            assert len(detail[OPERATOR_MONTH_TARGET_KEY]["cancelled_ids"]) == 3
            assert CONTRACT_QUOTA_MONTH_KEY not in detail
            assert await month_content_target(db, hospital_id, NOV, "PLAN_12") == 5
        # 월간 보고서·계약 이행 집계·당월 조정(워커)은 sync 판을 읽는다 — 같은 값이어야 한다.
        sync_engine = create_engine(make_url(require_db_url("SYNC_DATABASE_URL")))
        try:
            with Session(sync_engine) as sync_db:
                assert month_content_target_sync(sync_db, hospital_id, NOV, "PLAN_12") == 5
        finally:
            sync_engine.dispose()

        # 편수를 보내지 않는 저장(요일만 바꾼 저장·옛 탭)은 정한 편수를 지우지 않고 종료한 슬롯을 다시 사지 않는다.
        async with AsyncSession(engine, expire_on_commit=False) as db:
            again = await content_api.set_schedule(hospital_id, _body(None), db=db)
        assert again["month_target"] == 5
        assert again["slots_created"] == 0
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert len(await _live(db, hospital_id)) == 5
            assert await month_content_target(db, hospital_id, NOV, "PLAN_12") == 5

        # 다시 계약 편수로 올리면 이 기능이 종료한 슬롯만 빼고 새로 채운다(발행 요일 8일이라 하루 2편).
        async with AsyncSession(engine, expire_on_commit=False) as db:
            raised = await content_api.set_schedule(hospital_id, _body(12, daily_max=2), db=db)
        assert raised["month_target"] == 12
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert len(await _live(db, hospital_id)) == 12
            detail = await _last_audit(db, hospital_id)
            assert detail[CONTRACT_QUOTA_MONTH_KEY] == "2026-11"
            assert OPERATOR_MONTH_TARGET_KEY not in detail
            assert await month_content_target(db, hospital_id, NOV, "PLAN_12") == 12
    finally:
        await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_target_over_contract_quota_is_refused(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital_id = (await _seed(db)).id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            with pytest.raises(HTTPException) as exc:
                await content_api.set_schedule(hospital_id, _body(13), db=db)
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "MONTH_TARGET_OVER_CONTRACT"
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert len(await _live(db, hospital_id)) == 8
    finally:
        await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_only_the_first_contract_month_can_be_set(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital_id = (await _seed(db, published_october=True)).id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            with pytest.raises(HTTPException) as exc:
                await content_api.set_schedule(hospital_id, _body(5), db=db)
        assert exc.value.detail["code"] == "MONTH_TARGET_FIRST_MONTH_ONLY"
        async with AsyncSession(engine, expire_on_commit=False) as db:
            view = await content_api.get_schedule_month_target(hospital_id, month="2026-11", db=db)
        assert view["editable"] is False
    finally:
        await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_cannot_go_below_work_that_must_stay_and_nothing_is_written(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital_id = (await _seed(db, human_edited=6)).id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            with pytest.raises(HTTPException) as exc:
                await content_api.set_schedule(hospital_id, _body(2), db=db)
        assert exc.value.detail["code"] == "MONTH_TARGET_BELOW_KEPT"
        assert exc.value.detail["minimum"] == 6
        async with AsyncSession(engine, expire_on_commit=False) as db:
            # 저장이 커밋되지 않았다 — 슬롯도 그대로다.
            assert len(await _live(db, hospital_id)) == 8
    finally:
        await _cleanup(engine, hospital_id)
        await engine.dispose()


async def test_month_target_view_reports_quota_target_and_operator_value(frozen_today):
    engine = _engine()
    hospital_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            hospital_id = (await _seed(db)).id
        async with AsyncSession(engine, expire_on_commit=False) as db:
            before = await content_api.get_schedule_month_target(hospital_id, month="2026-11", db=db)
        # 시스템이 안분한 첫 달(8편)이 지금 값이다.
        assert before == {
            "month": "2026-11",
            "contract_quota": 12,
            "month_target": 8,
            "operator_target": None,
            "editable": True,
        }
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await content_api.set_schedule(hospital_id, _body(6), db=db)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            after = await content_api.get_schedule_month_target(hospital_id, month="2026-11", db=db)
        assert (after["month_target"], after["operator_target"]) == (6, 6)
    finally:
        await _cleanup(engine, hospital_id)
        await engine.dispose()
