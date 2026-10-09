"""Real PostgreSQL proof for the bounded historical publication-notification drain."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import NotificationOutbox, NotificationOutboxState
from app.services.content_publish_delivery import (
    LEGACY_PUBLISH_NOTIFICATION_TYPE,
    legacy_publish_dedupe_key,
)
from app.services.content_publish_reconciliation import (
    LegacyPublishRetirementBlocked,
    drain_legacy_publish_history,
    inspect_legacy_publish_history,
    require_legacy_publish_history_drained,
)
from tests.db_env import require_db_url


def _database_url() -> str:
    return require_db_url("CONTENT_PUBLISH_RECOVERY_DATABASE_URL")


async def _history_digest(
    sessions: async_sessionmaker,
    row_ids: tuple[uuid.UUID, ...],
) -> str:
    async with sessions() as db:
        rows = tuple(
            (
                await db.execute(
                    select(
                        NotificationOutbox.id,
                        NotificationOutbox.state,
                        NotificationOutbox.sent_at,
                    )
                    .where(NotificationOutbox.id.in_(row_ids))
                    .order_by(NotificationOutbox.id)
                )
            ).all()
        )
    return hashlib.sha256(repr(rows).encode()).hexdigest()


@pytest.mark.asyncio
async def test_pending_abort_preserves_every_historical_row() -> None:
    engine = create_async_engine(_database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    row_id = uuid.uuid4()
    try:
        async with sessions() as db:
            db.add(
                NotificationOutbox(
                    id=row_id,
                    dedupe_key=f"TASK15-LEGACY-PENDING-{row_id}",
                    notification_type=LEGACY_PUBLISH_NOTIFICATION_TYPE,
                    channel="SLACK",
                    state=NotificationOutboxState.PENDING.value,
                    payload={"text": "synthetic retirement fixture"},
                    fallback_text="synthetic retirement fixture",
                    max_attempts=3,
                    next_attempt_at=datetime(2099, 1, 1, tzinfo=UTC),
                )
            )
            await db.commit()
        before = await _history_digest(sessions, (row_id,))

        with pytest.raises(LegacyPublishRetirementBlocked) as blocked:
            await drain_legacy_publish_history(sessions)

        assert blocked.value.inventory.open_transport >= 1
        assert await _history_digest(sessions, (row_id,)) == before
        async with sessions() as db:
            assert await db.get(NotificationOutbox, row_id) is not None
    finally:
        async with sessions() as db:
            await db.execute(delete(NotificationOutbox).where(NotificationOutbox.id == row_id))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_idempotent_drain_reaches_double_zero_without_deleting_history() -> None:
    engine = create_async_engine(_database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    content_id = uuid.uuid4()
    published_at = datetime(2026, 8, 10, tzinfo=UTC)
    outbox_id: uuid.UUID | None = None
    try:
        async with sessions() as db:
            hospital = Hospital(
                id=hospital_id,
                name="Task15 legacy drain fixture",
                slug=f"task15-drain-{hospital_id.hex[:10]}",
                status=HospitalStatus.ACTIVE,
            )
            schedule = ContentSchedule(
                id=schedule_id,
                hospital_id=hospital_id,
                plan="PLAN_12",
                publish_days=[0],
                active_from=date(2026, 8, 1),
            )
            content = ContentItem(
                id=content_id,
                hospital_id=hospital_id,
                schedule_id=schedule_id,
                content_type=ContentType.FAQ,
                sequence_no=1,
                total_count=12,
                title="historical publication",
                body="historical publication body",
                scheduled_date=date(2026, 8, 10),
                status=ContentStatus.PUBLISHED,
                published_at=published_at,
                published_by="TASK15_FIXTURE",
            )
            outbox = NotificationOutbox(
                hospital_id=hospital_id,
                dedupe_key=legacy_publish_dedupe_key(content_id, published_at),
                notification_type=LEGACY_PUBLISH_NOTIFICATION_TYPE,
                channel="SLACK",
                state=NotificationOutboxState.SENT.value,
                payload={"text": "historical publication fixture"},
                fallback_text="historical publication fixture",
                attempt_count=1,
                max_attempts=3,
                sent_at=published_at,
                next_attempt_at=None,
            )
            db.add_all((hospital, schedule, content, outbox))
            await db.commit()
            outbox_id = outbox.id

        assert outbox_id is not None
        before_rows = await _history_digest(sessions, (outbox_id,))
        before = await inspect_legacy_publish_history(sessions)
        assert before.unapplied_sent >= 1

        assert await drain_legacy_publish_history(sessions) == 1
        after_first = await _history_digest(sessions, (outbox_id,))
        assert after_first == before_rows
        assert await drain_legacy_publish_history(sessions) == 0
        after_second = await _history_digest(sessions, (outbox_id,))
        assert after_second == before_rows

        ready = await require_legacy_publish_history_drained(sessions)
        assert ready.open_transport == 0
        assert ready.unapplied_sent == 0
        async with sessions() as db:
            assert await db.get(NotificationOutbox, outbox_id) is not None
            assert await db.scalar(
                select(func.count(AdminAuditLog.id)).where(
                    AdminAuditLog.action == "post_publish_notification_delivery_applied",
                    AdminAuditLog.target_type == "notification_outbox",
                    AdminAuditLog.target_id == str(outbox_id),
                )
            ) == 1
    finally:
        async with sessions() as db:
            if outbox_id is not None:
                await db.execute(
                    delete(NotificationOutbox).where(NotificationOutbox.id == outbox_id)
                )
            await db.execute(delete(Hospital).where(Hospital.id == hospital_id))
            await db.commit()
        await engine.dispose()


def test_no_live_producer_or_recurring_consumer() -> None:
    app_root = Path(__file__).parents[2] / "app"
    excluded = {
        app_root / "services" / "content_publish_delivery.py",
        app_root / "services" / "content_publish_reconciliation.py",
        app_root / "utils" / "legacy_publish_retirement_preflight.py",
    }
    sources = "\n".join(
        path.read_text()
        for path in app_root.rglob("*.py")
        if path not in excluded
    )

    assert "build_publish_notification_intent(" not in sources
    assert "reconcile_sent_publish_notifications" not in sources
    assert "apply_legacy_publish_delivery" not in sources
