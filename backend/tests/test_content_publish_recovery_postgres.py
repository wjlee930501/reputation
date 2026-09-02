"""Real PostgreSQL proof for publication notification failure and recovery."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.operations import (
    Incident,
    IncidentState,
    NotificationOutbox,
    NotificationOutboxState,
)
from app.services import notification_delivery
from app.services.content_publish_notifications import (
    PUBLISH_NOTIFICATION_TYPE,
    build_publish_notification_intent,
)
from app.services.content_publish_reconciliation import reconcile_sent_publish_notifications
from app.services.incidents import mark_retrying
from app.services.notification_outbox import (
    dispatch_notification_batch,
    enqueue_notification,
    retry_notification,
)

_ASYNC_URL = "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"


@pytest.mark.asyncio
async def test_failed_manual_publish_notification_recovers_without_republish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(_ASYNC_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    content_id = uuid.uuid4()
    slug = f"ops-qa-t15-{hospital_id.hex[:10]}"
    published_at = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)

    try:
        async with sessions() as db:
            hospital = Hospital(
                id=hospital_id,
                name="Task15 테스트의원",
                slug=slug,
                status=HospitalStatus.ACTIVE,
                plan=Plan.PLAN_12,
                site_live=True,
            )
            schedule = ContentSchedule(
                id=schedule_id,
                hospital_id=hospital_id,
                plan="PLAN_12",
                publish_days=[0, 2, 4],
                active_from=date(2026, 8, 1),
            )
            item = ContentItem(
                id=content_id,
                hospital_id=hospital_id,
                schedule_id=schedule_id,
                content_type=ContentType.FAQ,
                sequence_no=1,
                total_count=12,
                title="진료 전 확인할 점",
                body="증상에 따라 진료 방향을 설명합니다.",
                scheduled_date=date(2026, 8, 10),
                status=ContentStatus.PUBLISHED,
                published_at=published_at,
                published_by="SYSTEM_MANUAL_RECOVERY",
            )
            db.add_all((hospital, schedule, item))
            await db.flush()
            outbox = await enqueue_notification(
                db, build_publish_notification_intent(item, hospital)
            )
            outbox_id = outbox.id
            await db.commit()

        failed_calls = 0

        def fail_transport(_request: httpx.Request) -> httpx.Response:
            nonlocal failed_calls
            failed_calls += 1
            return httpx.Response(500, json={"ok": False})

        async with httpx.AsyncClient(transport=httpx.MockTransport(fail_transport)) as client:
            first = await dispatch_notification_batch(
                sessions,
                client,
                webhook_url="https://hooks.slack.com/services/test/task15",
                worker_id="task15-fail",
                throttle=_no_pause,
            )
            second_retry = await dispatch_notification_batch(
                sessions,
                client,
                webhook_url="https://hooks.slack.com/services/test/task15",
                worker_id="task15-fail-2",
                now=datetime.now(UTC) + timedelta(minutes=2),
                throttle=_no_pause,
            )
            third = await dispatch_notification_batch(
                sessions,
                client,
                webhook_url="https://hooks.slack.com/services/test/task15",
                worker_id="task15-fail-3",
                now=datetime.now(UTC) + timedelta(minutes=5),
                throttle=_no_pause,
            )
        assert (first.retried, second_retry.retried, third.failed) == (1, 1, 1)
        assert failed_calls == 3

        # 실패로 소진된 행을 다시 살리는 유일한 프로덕션 경로는 Admin의 수동 재시도다
        # (api/admin이 호출하는 notification_store.retry_notification). 테스트도 같은
        # 경로를 써야 "FAILED → RETRYING → 재발송"이 실제로 가능한지 증명한다.
        async with sessions() as db:
            failed = await db.get(NotificationOutbox, outbox_id)
            assert failed is not None
            assert failed.state == NotificationOutboxState.FAILED.value
            retried = await retry_notification(
                db,
                failed.id,
                expected_version=failed.version,
                actor="ae-operator",
                reason="Slack 전송 실패 확인 후 수동 재시도",
            )
            assert isinstance(retried, NotificationOutbox)
            await db.commit()

        class HookFailure(RuntimeError):
            pass

        original_hook = notification_delivery.run_notification_success_hook
        hook_calls = 0

        async def fail_first_after_sent(*args: object) -> None:
            nonlocal hook_calls
            hook_calls += 1
            if hook_calls == 1:
                raise HookFailure("domain hook temporarily unavailable")
            await original_hook(*args)

        monkeypatch.setattr(
            notification_delivery,
            "run_notification_success_hook",
            fail_first_after_sent,
        )
        async with sessions() as db:
            db.add(
                NotificationOutbox(
                    hospital_id=hospital_id,
                    dedupe_key=f"TASK15-NEXT-{hospital_id}",
                    notification_type="TASK15_NEXT_ROW",
                    channel="SLACK",
                    state=NotificationOutboxState.PENDING.value,
                    payload={"text": "다음 알림도 처리"},
                    fallback_text="다음 알림도 처리",
                    max_attempts=1,
                    next_attempt_at=datetime.now(UTC),
                )
            )
            await db.commit()
        success_calls = 0

        def succeed_transport(_request: httpx.Request) -> httpx.Response:
            nonlocal success_calls
            success_calls += 1
            return httpx.Response(200, text="ok")

        async with httpx.AsyncClient(transport=httpx.MockTransport(succeed_transport)) as client:
            second = await dispatch_notification_batch(
                sessions,
                client,
                webhook_url="https://hooks.slack.com/services/test/task15",
                worker_id="task15-success",
                throttle=_no_pause,
            )
        assert second.claimed == 2
        assert second.sent == 2
        assert success_calls == 2
        assert hook_calls == 2

        async with sessions() as db:
            sent_outbox = await db.get(NotificationOutbox, outbox_id)
            item = await db.get(ContentItem, content_id)
            incident = await db.scalar(
                select(Incident).where(
                    Incident.source_type == "NOTIFICATION_OUTBOX",
                    Incident.source_id == str(outbox_id),
                )
            )
            assert sent_outbox is not None and sent_outbox.state == "SENT"
            assert item is not None and item.post_publish_notified_at is None
            assert incident is not None and incident.state == IncidentState.OPEN.value

            malformed = NotificationOutbox(
                hospital_id=hospital_id,
                dedupe_key=f"{PUBLISH_NOTIFICATION_TYPE}:malformed",
                notification_type=PUBLISH_NOTIFICATION_TYPE,
                channel="SLACK",
                state=NotificationOutboxState.SENT.value,
                payload={"text": "잘못된 과거 식별자"},
                fallback_text="잘못된 과거 식별자",
                attempt_count=1,
                max_attempts=1,
                next_attempt_at=None,
                sent_at=datetime.now(UTC),
            )
            db.add(malformed)
            await db.commit()
            malformed_id = malformed.id

        async with sessions() as stale_db:
            stale_incident = await stale_db.scalar(
                select(Incident).where(
                    Incident.source_type == "NOTIFICATION_OUTBOX",
                    Incident.source_id == str(outbox_id),
                )
            )
            assert stale_incident is not None and stale_incident.state == IncidentState.OPEN.value
            async with sessions() as concurrent_db:
                current = await concurrent_db.get(Incident, stale_incident.id)
                assert current is not None
                changed = await mark_retrying(
                    concurrent_db,
                    current.id,
                    expected_version=current.version,
                    actor="concurrent-worker",
                    reason="operator requested retry",
                )
                assert isinstance(changed, Incident)
                await concurrent_db.commit()

            @asynccontextmanager
            async def stale_sessions() -> AsyncIterator[AsyncSession]:
                yield stale_db

            assert await reconcile_sent_publish_notifications(stale_sessions) == 1

        async with sessions() as db:
            sent_outbox = await db.get(NotificationOutbox, outbox_id)
            item = await db.get(ContentItem, content_id)
            incident = await db.scalar(
                select(Incident).where(
                    Incident.source_type == "NOTIFICATION_OUTBOX",
                    Incident.source_id == str(outbox_id),
                )
            )
            assert sent_outbox is not None and item is not None and incident is not None
            assert item.post_publish_notified_at == sent_outbox.sent_at
            assert item.published_at == published_at
            assert item.published_by == "SYSTEM_MANUAL_RECOVERY"
            assert incident.state == IncidentState.RECOVERED.value
            assert await db.scalar(
                select(func.count(Incident.id)).where(Incident.hospital_id == hospital_id)
            ) == 1
            assert await db.scalar(
                select(func.count(AdminAuditLog.id)).where(
                    AdminAuditLog.action == "post_publish_notification_delivery_applied",
                    AdminAuditLog.target_type == "notification_outbox",
                    AdminAuditLog.target_id == str(malformed_id),
                )
            ) == 0

        async with httpx.AsyncClient(transport=httpx.MockTransport(succeed_transport)) as client:
            replay = await dispatch_notification_batch(
                sessions,
                client,
                webhook_url="https://hooks.slack.com/services/test/task15",
                worker_id="task15-no-replay",
                throttle=_no_pause,
            )
        assert replay.claimed == 0
        assert success_calls == 2
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                text("DELETE FROM incidents WHERE source_type='NOTIFICATION_OUTBOX' AND source_id IN (SELECT id::text FROM notification_outbox WHERE hospital_id=:hospital_id)"),
                {"hospital_id": hospital_id},
            )
            await cleanup.execute(
                text("DELETE FROM notification_outbox WHERE hospital_id=:hospital_id"),
                {"hospital_id": hospital_id},
            )
            await cleanup.execute(
                text("DELETE FROM hospitals WHERE id=:hospital_id"),
                {"hospital_id": hospital_id},
            )
            await cleanup.commit()
        await engine.dispose()


async def _no_pause() -> None:
    return None
