from __future__ import annotations

import json
import uuid
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
from app.workers import task_incident_control


def test_untracked_task_failure_does_not_emit_an_unrecoverable_alert() -> None:
    """Given no durable run identity, a failure must not raise or create permanent Slack noise.

    The signal handler only ever projects into `task_incident_control`
    (Slack for a task failure goes through the durable incident/outbox path,
    not a direct notifier call), so an untracked task must resolve quietly.
    """

    celery_module._alert_on_task_failure(
        sender=type("UntrackedTask", (), {"name": "tests.untracked"})(),
        task_id="untracked-task-id",
        exception=RuntimeError("private@example.com"),
    )


def test_classified_generation_run_suppresses_generic_failure_slack(monkeypatch) -> None:
    run_id = uuid.uuid4()
    task = SimpleNamespace(request=SimpleNamespace(headers={"operation_run_id": str(run_id)}))
    run = SimpleNamespace(
        id=run_id,
        operation_type="REGENERATE_CONTENT",
        safe_error_code="GENERATION_REJECTED",
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", FakeSession)
    monkeypatch.setattr(task_incident_control, "_tracked_run", lambda *_args: run)
    monkeypatch.setattr(
        task_incident_control,
        "_open_incident",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("generic incident must not be opened after classification")
        ),
    )

    assert task_incident_control.record_task_failure(task, "worker-task") is False


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
        assert task_incident_control.record_task_failure(celery_task, run.task_id) is True
        assert task_incident_control.record_task_failure(celery_task, run.task_id) is True
        with sync_factory() as db:
            incident = db.scalar(
                select(Incident).where(Incident.operation_run_id == run.id)
            )
            assert incident is not None
            incident_ids.append(incident.id)
            assert incident.state == "OPEN"
            assert incident.occurrence_count == 3
            assert incident.episode_seq == 1
            assert "작업 다시 시도" in incident.next_action
            open_notice = db.scalar(
                select(NotificationOutbox).where(NotificationOutbox.incident_id == incident.id)
            )
            assert open_notice is not None
            assert open_notice.dedupe_key.endswith(":e1")
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
            assert sorted(notice.notification_type for notice in notices) == [
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
            assert reopened.episode_seq == 2
            assert reopened.acknowledged_at is None
            open_notices = list(
                db.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.incident_id == reopened.id,
                        NotificationOutbox.notification_type == "INCIDENT_OPEN",
                    )
                )
            )
            assert sorted(notice.dedupe_key.rsplit(":", 1)[-1] for notice in open_notices) == [
                "e1",
                "e2",
            ]
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
