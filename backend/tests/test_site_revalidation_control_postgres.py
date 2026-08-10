"""Real PostgreSQL proof for durable post-publication cache refresh recovery."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.operations import Incident, IncidentState, NotificationOutbox, OperationRun
from app.services import site_revalidation_control as control

_ASYNC_URL = os.getenv(
    "TASK19_ASYNC_DATABASE_URL",
    "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test",
)


@pytest.mark.asyncio
async def test_cache_refresh_failure_escalates_once_without_undoing_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(_ASYNC_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    content_id = uuid.uuid4()
    recovery_content_id = uuid.uuid4()
    published_at = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    slug = f"task19-cache-{hospital_id.hex[:10]}"
    monkeypatch.setattr(control, "get_async_sessionmaker", lambda: sessions)

    try:
        async with sessions() as db:
            db.add(
                Hospital(
                    id=hospital_id,
                    name="Task19 캐시 테스트의원",
                    slug=slug,
                    status=HospitalStatus.ACTIVE,
                    plan=Plan.PLAN_12,
                    site_live=True,
                )
            )
            db.add(
                ContentSchedule(
                    id=schedule_id,
                    hospital_id=hospital_id,
                    plan=Plan.PLAN_12,
                    publish_days=[0, 2, 4],
                    active_from=date(2026, 8, 1),
                )
            )
            for item_id, sequence in ((content_id, 1), (recovery_content_id, 2)):
                db.add(
                    ContentItem(
                        id=item_id,
                        hospital_id=hospital_id,
                        schedule_id=schedule_id,
                        content_type=ContentType.FAQ,
                        sequence_no=sequence,
                        total_count=12,
                        title=f"공개 글 {sequence}",
                        body="공개된 본문은 캐시 실패와 무관하게 보존됩니다.",
                        scheduled_date=date(2026, 8, 10),
                        status=ContentStatus.PUBLISHED,
                        published_at=published_at,
                        published_by="Task19",
                    )
                )
            await db.commit()

        first = await control.start_revalidation_failure(f"  {slug.upper()}  ", content_id)
        duplicate = await control.start_revalidation_failure(slug, content_id)
        assert first is not None and first.created is True and first.delay_seconds == 60
        assert duplicate is not None and duplicate.created is False
        assert (await control.record_retry_failure(first.run_id)).delay_seconds == 300
        assert (await control.record_retry_failure(first.run_id)).delay_seconds == 900
        terminal = await control.record_retry_failure(first.run_id)
        assert terminal is not None and terminal.operator_action_required is True
        assert await control.record_retry_failure(first.run_id) is None

        recovery = await control.start_revalidation_failure(slug, recovery_content_id)
        assert recovery is not None
        assert await control.record_revalidation_success(recovery.run_id) is True
        assert await control.record_revalidation_success(recovery.run_id) is False

        async with sessions() as db:
            published = await db.get(ContentItem, content_id)
            terminal_run = await db.get(OperationRun, first.run_id)
            incidents = list(
                (
                    await db.execute(select(Incident).where(Incident.hospital_id == hospital_id))
                ).scalars()
            )
            outbox_count = await db.scalar(
                select(func.count(NotificationOutbox.id)).where(
                    NotificationOutbox.hospital_id == hospital_id
                )
            )
            assert published is not None
            assert published.status == ContentStatus.PUBLISHED
            assert published.published_at == published_at
            assert terminal_run is not None and terminal_run.failure_count == 1
            assert sorted(incident.state for incident in incidents) == [
                IncidentState.OPEN.value,
                IncidentState.RECOVERED.value,
            ]
            assert outbox_count == 1
    finally:
        async with sessions() as db:
            incident_ids = select(Incident.id).where(Incident.hospital_id == hospital_id)
            await db.execute(
                delete(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
            )
            await db.execute(delete(Incident).where(Incident.id.in_(incident_ids)))
            await db.execute(delete(OperationRun).where(OperationRun.hospital_id == hospital_id))
            await db.execute(delete(ContentItem).where(ContentItem.hospital_id == hospital_id))
            await db.execute(
                delete(ContentSchedule).where(ContentSchedule.hospital_id == hospital_id)
            )
            await db.execute(delete(Hospital).where(Hospital.id == hospital_id))
            await db.commit()
        await engine.dispose()
