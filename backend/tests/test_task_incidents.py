from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from operation_run_signal_support import (
    SYNC_DATABASE_URL,
    RecordingTask,
    dispatch_test_run,
)
from operation_run_signal_support import (
    signal_store as _signal_store_fixture,  # noqa: F401
)
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core import celery_app as celery_module
from app.models.admin_user import AdminUser
from app.models.operations import Incident, IncidentState, NotificationOutbox
from app.services import notifier
from app.workers import task_incident_control


def test_untracked_task_failure_does_not_emit_an_unrecoverable_alert(monkeypatch) -> None:
    """Given no durable run identity, a failure must not create permanent Slack noise."""

    delivered: list[dict[str, str]] = []

    async def record_failure(**payload: str) -> bool:
        delivered.append(payload)
        return True

    monkeypatch.setattr(notifier, "notify_task_failure", record_failure)

    celery_module._alert_on_task_failure(
        sender=type("UntrackedTask", (), {"name": "tests.untracked"})(),
        task_id="untracked-task-id",
        exception=RuntimeError("private@example.com"),
    )

    assert delivered == []


@pytest.mark.asyncio
async def test_exact_run_failure_opens_then_same_run_success_recovers(
    signal_store,
    monkeypatch,
) -> None:
    factory, hospital_id = signal_store
    dispatched = RecordingTask()
    run = await dispatch_test_run(factory, hospital_id, dispatched, "task20-recovery")
    celery_task = SimpleNamespace(
        request=SimpleNamespace(headers={"operation_run_id": str(run.id)})
    )
    sync_engine = create_engine(SYNC_DATABASE_URL)
    sync_factory = sessionmaker(sync_engine, expire_on_commit=False, class_=Session)
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", sync_factory)
    audit_actions: list[str] = []
    monkeypatch.setattr(
        task_incident_control,
        "_audit",
        lambda _db, _incident, action: audit_actions.append(action),
    )
    incident_ids = []
    admin_ids = []
    try:
        assert task_incident_control.record_task_failure(celery_task, run.task_id) is True
        with sync_factory() as db:
            incident = db.scalar(
                select(Incident).where(Incident.operation_run_id == run.id)
            )
            assert incident is not None
            incident_ids.append(incident.id)
            assert incident.state == "OPEN"
            assert "작업 다시 시도" in incident.next_action
            open_notice = db.scalar(
                select(NotificationOutbox).where(NotificationOutbox.incident_id == incident.id)
            )
            assert open_notice is not None
            rendered = json.dumps(open_notice.payload, ensure_ascii=False)
            assert all(
                label in rendered
                for label in ("무슨 문제인지", "고객 영향", "지금 할 일")
            )
            assert "운영센터에서 조치하기" in rendered
            assert "개발팀에 전달할 정보" in rendered
            assert "private@example.com" not in rendered

        assert task_incident_control.record_task_success(celery_task, "unrelated-task") is False
        with sync_factory() as db:
            still_open = db.get(Incident, incident_ids[0])
            assert still_open is not None and still_open.state == "OPEN"

        assert task_incident_control.record_task_success(celery_task, run.task_id) is True
        with sync_factory() as db:
            recovered = db.get(Incident, incident_ids[0])
            assert recovered is not None
            assert recovered.state == "RECOVERED"
            assert recovered.recovered_at is not None
            notices = list(
                db.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.incident_id == recovered.id
                    )
                )
            )
            assert [notice.notification_type for notice in notices] == [
                "INCIDENT_OPEN",
                "INCIDENT_RECOVERED",
            ]
            assert audit_actions[-2:] == ["incident_retrying", "incident_recovered"]
        assert task_incident_control.record_task_success(celery_task, run.task_id) is False

        with sync_factory() as db:
            reopened = db.get(Incident, incident_ids[0])
            assert reopened is not None
            admin_id = db.scalar(select(AdminUser.id))
            if admin_id is None:
                admin = AdminUser(
                    email=f"task20-{run.id}@example.test",
                    name="Task20 운영자",
                    password_hash="not-a-real-password-hash",
                )
                db.add(admin)
                db.flush()
                admin_id = admin.id
                admin_ids.append(admin_id)
            reopened.state = IncidentState.ACKNOWLEDGED.value
            reopened.acknowledged_at = datetime.now(UTC)
            reopened.acknowledged_by_id = admin_id
            reopened.version += 1
            db.commit()
        assert task_incident_control.record_task_failure(celery_task, run.task_id) is True
        with sync_factory() as db:
            reopened = db.get(Incident, incident_ids[0])
            assert reopened is not None
            assert reopened.state == IncidentState.OPEN.value
            assert reopened.acknowledged_at is None
            stale_version = reopened.version - 1
            stale = task_incident_control._transition_incident(
                db,
                type(reopened)(
                    id=reopened.id,
                    version=stale_version,
                    state=IncidentState.OPEN.value,
                ),
                expected_state=IncidentState.OPEN,
                next_state=IncidentState.RETRYING,
            )
            assert stale is None
            db.rollback()
    finally:
        with sync_factory() as db:
            if incident_ids:
                db.execute(
                    delete(NotificationOutbox).where(
                        NotificationOutbox.incident_id.in_(incident_ids)
                    )
                )
                db.execute(delete(Incident).where(Incident.id.in_(incident_ids)))
            if admin_ids:
                db.execute(delete(AdminUser).where(AdminUser.id.in_(admin_ids)))
            db.commit()
        sync_engine.dispose()
