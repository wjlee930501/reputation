"""월간 측정 사고는 레지스트리로 RETRYING+기한으로 열리고, 열림 알림은 판정 시각까지 보류된다(Phase 1-6a)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, NotificationOutbox
from app.services.monthly_period import KST, monthly_final_judgment_at
from app.services.notification_store import RESOLVED_BEFORE_SEND_CODE, claim_notification_batch
from app.workers import weekly_sov_incident_control as control

# 알림 행의 다음 시도 시각은 실제 시계라, 발송 판정을 볼 수 있게 미래 달로 둔다.
PERIOD = "2099-09"
DAY_ONE = datetime(2099, 10, 1, 0, 20, tzinfo=KST)
JUDGMENT = monthly_final_judgment_at(2099, 9)


def _bind(monkeypatch, db, *, now: datetime) -> None:
    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(control, "get_async_sessionmaker", lambda: _session)
    real_open = control.open_or_touch_incident

    async def _open_at(session, request, **kwargs):
        kwargs["now"] = now
        return await real_open(session, request, **kwargs)

    monkeypatch.setattr(control, "open_or_touch_incident", _open_at)


async def _hospital(db) -> Hospital:
    hospital = Hospital(
        name="월간보류의원", slug=f"monthly-hold-{uuid.uuid4().hex[:10]}", status=HospitalStatus.ACTIVE
    )
    db.add(hospital)
    await db.flush()
    return hospital


async def _incident(db, incident_id) -> Incident:
    return await db.scalar(
        select(Incident).where(Incident.id == incident_id).execution_options(populate_existing=True)
    )


async def _notices(db, incident_id) -> list[NotificationOutbox]:
    return list(
        (
            await db.execute(
                select(NotificationOutbox)
                .where(NotificationOutbox.incident_id == incident_id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def test_day_one_incomplete_measurement_is_retrying_and_its_notice_waits(
    pg_async_session, monkeypatch
):
    db = pg_async_session
    hospital = await _hospital(db)
    _bind(monkeypatch, db, now=DAY_ONE)

    incident_id = await control.open_monthly_sov_failure(
        hospital_id=hospital.id,
        hospital_name=hospital.name,
        period_key=PERIOD,
        error_code="MONTHLY_SOV_MEASUREMENT_INCOMPLETE",
    )

    incident = await _incident(db, incident_id)
    assert incident.state == "RETRYING"
    assert incident.sla_due_at == JUDGMENT
    [notice] = await _notices(db, incident_id)
    # 7일 밤 복구 마감 직후(8일 00:00~00:15)에도 보류된다 — 00:15 최종화가 닫을 기회를 갖는다.
    for held_at in (DAY_ONE, datetime(2099, 10, 8, 0, 5, tzinfo=KST)):
        claimed = await claim_notification_batch(db, "test-worker", now=held_at)
        assert notice.id not in {row.id for row in claimed}
    # 최종화 뒤에도 남은 실패는 판정 시각에 사람에게 넘어간다.
    claimed = await claim_notification_batch(db, "test-worker", now=JUDGMENT + timedelta(minutes=1))
    assert notice.id in {row.id for row in claimed}


async def test_day_eight_close_settles_the_held_notice(pg_async_session, monkeypatch):
    db = pg_async_session
    hospital = await _hospital(db)
    _bind(monkeypatch, db, now=DAY_ONE)
    incident_id = await control.open_monthly_sov_failure(
        hospital_id=hospital.id,
        hospital_name=hospital.name,
        period_key=PERIOD,
        error_code="MONTHLY_SOV_MEASUREMENT_PARTIAL",
    )

    closed = await control.recover_monthly_sov_failure(hospital_id=hospital.id, period_key=PERIOD)
    await claim_notification_batch(db, "test-worker", now=JUDGMENT + timedelta(minutes=1))

    assert closed is True
    assert (await _incident(db, incident_id)).state == "RECOVERED"
    [notice] = await _notices(db, incident_id)
    assert notice.state == "FAILED"
    assert notice.safe_error_code == RESOLVED_BEFORE_SEND_CODE


async def test_human_cause_opens_at_once_and_pages(pg_async_session, monkeypatch):
    db = pg_async_session
    hospital = await _hospital(db)
    _bind(monkeypatch, db, now=DAY_ONE)
    cost_blocked = await control.open_monthly_sov_failure(
        hospital_id=hospital.id,
        hospital_name=hospital.name,
        period_key=PERIOD,
        error_code="MONTHLY_SOV_COST_GUARD_BLOCKED",
    )
    assert (await _incident(db, cost_blocked)).state == "RETRYING"
    assert await _notices(db, cost_blocked) == []

    _bind(monkeypatch, db, now=DAY_ONE + timedelta(hours=6))
    same = await control.open_monthly_sov_failure(
        hospital_id=hospital.id,
        hospital_name=hospital.name,
        period_key=PERIOD,
        error_code="MONTHLY_SOV_NO_MEASUREMENT_MANIFEST",
    )

    assert same == cost_blocked
    assert (await _incident(db, same)).state == "OPEN"
    [notice] = await _notices(db, same)
    claimed = await claim_notification_batch(db, "test-worker", now=DAY_ONE + timedelta(hours=7))
    assert notice.id in {row.id for row in claimed}
