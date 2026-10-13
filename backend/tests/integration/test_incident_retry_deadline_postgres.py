"""주인 없는 '복구 중'을 없앤다 — 기한 없는 RETRYING은 24시간 뒤 사람의 일(Phase 1-3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.operations import Incident
from app.services.incidents import mark_retrying
from app.workers.incident_backlog import settle_incidents

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


def _incident(state="OPEN", *, sla=None, incident_type="NAVER_SOURCE_FETCH_FAILED", code=None, updated=None):
    return Incident(
        dedupe_key=f"deadline-test:{uuid.uuid4()}",
        incident_type=incident_type,
        state=state,
        severity="HIGH",
        customer_impact="테스트",
        source_type="TEST",
        next_action="테스트",
        admin_path="/operations",
        sla_due_at=sla,
        safe_error_code=code,
        first_seen_at=NOW - timedelta(days=3),
        last_seen_at=NOW - timedelta(days=3),
        updated_at=updated or NOW - timedelta(days=3),
    )


async def _retry(db, incident):
    db.add(incident)
    await db.flush()
    return await mark_retrying(
        db, incident.id, expected_version=incident.version, actor="test", reason="test", now=NOW
    )


async def test_retrying_without_a_deadline_gets_the_default_window(pg_async_session):
    result = await _retry(pg_async_session, _incident())

    assert result.state == "RETRYING"
    assert result.sla_due_at == NOW + timedelta(hours=24)


async def test_a_future_deadline_is_kept(pg_async_session):
    due = NOW + timedelta(hours=6)
    result = await _retry(pg_async_session, _incident(sla=due))

    assert result.sla_due_at == due


async def test_a_past_deadline_from_an_old_episode_is_replaced(pg_async_session):
    result = await _retry(pg_async_session, _incident(sla=NOW - timedelta(days=10)))

    assert result.sla_due_at == NOW + timedelta(hours=24)


def test_existing_unbounded_retrying_rows_get_a_deadline_from_their_last_change(pg_conn):
    db = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    stuck = _incident("RETRYING", updated=NOW - timedelta(days=2))
    owned_elsewhere = _incident(
        "RETRYING", incident_type="CONTENT_GENERATION_FAILED", code="MISSING_APPROVED_ESSENCE"
    )
    db.add_all([stuck, owned_elsewhere])
    db.flush()

    settle_incidents(db, now=NOW)

    db.expire_all()
    assert db.get(Incident, stuck.id).sla_due_at == NOW - timedelta(days=1)
    # 사람의 일을 별도 ESCALATED 사고가 가진 생성 보류는 기한 없이 둔다.
    assert db.get(Incident, owned_elsewhere.id).sla_due_at is None
    actions = db.scalars(
        select(AdminAuditLog.action).where(AdminAuditLog.target_id == str(stuck.id))
    ).all()
    assert "incident_retry_deadline_defaulted" in actions
    db.close()
