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
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core import celery_app as celery_module
from app.models.admin_user import AdminUser
from app.models.handoff import HospitalHandoff
from app.models.operations import Incident, IncidentState, NotificationOutbox
from app.workers import task_incident_control


def test_untracked_task_failure_does_not_emit_an_unrecoverable_alert(monkeypatch) -> None:
    """Given no durable run identity, a failure must not raise or create permanent Slack noise.

    The signal handler only ever projects into `task_incident_control`
    (Slack for a task failure goes through the durable incident/outbox path,
    not a direct notifier call), so an untracked task must resolve quietly.
    """
    enqueued: list[object] = []
    monkeypatch.setattr(
        task_incident_control,
        "_enqueue",
        lambda _db, intent: enqueued.append(intent),
    )

    celery_module._alert_on_task_failure(
        sender=type("UntrackedTask", (), {"name": "tests.untracked"})(),
        task_id="untracked-task-id",
        exception=RuntimeError("private@example.com"),
    )

    assert enqueued == []


def test_terminal_outcome_identity_separates_months_and_keeps_retry_scope() -> None:
    hospital_id = uuid.uuid4()

    def monthly_run(run_id: uuid.UUID, year: int, month: int) -> SimpleNamespace:
        return SimpleNamespace(
            id=run_id,
            hospital_id=hospital_id,
            operation_type="RUN_SOV",
            safe_error_code="TASK_FAILED",
            idempotency_key=f"monthly-sov:{hospital_id}:{year:04d}-{month:02d}",
            request_payload={
                "_dispatch": {
                    "target_type": "hospital",
                    "target_id": str(hospital_id),
                    "queue": "sov",
                    "task_args": [str(hospital_id), "monthly", year, month],
                }
            },
            result_summary={"measurement_month": f"{year:04d}-{month:02d}"},
        )

    august_first = task_incident_control._terminal_outcome_identity(
        monthly_run(uuid.uuid4(), 2026, 8)
    )
    august_retry = task_incident_control._terminal_outcome_identity(
        monthly_run(uuid.uuid4(), 2026, 8)
    )
    september = task_incident_control._terminal_outcome_identity(
        monthly_run(uuid.uuid4(), 2026, 9)
    )

    assert august_first is not None
    assert august_retry is not None
    assert september is not None
    assert august_first.dedupe_key == august_retry.dedupe_key
    assert august_first.source_id == august_retry.source_id
    assert august_first.dedupe_key != september.dedupe_key
    assert august_first.period == "2026-08"
    assert september.period == "2026-09"


def test_classified_domain_failure_is_not_reprojected_as_terminal_transport_failure() -> None:
    run = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        operation_type="REGENERATE_CONTENT",
        safe_error_code="GENERATION_REJECTED",
        idempotency_key=None,
        request_payload={},
        result_summary=None,
    )

    assert task_incident_control._terminal_outcome_identity(run) is None


def test_unowned_classified_failure_is_never_silently_dropped() -> None:
    run = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=None,
        operation_type="MONTHLY_SOV_PERIOD",
        safe_error_code="PERIOD_FINALIZATION_FAILED",
        idempotency_key="monthly-sov-period:2026-08",
        request_payload={"source_type": "MONTHLY_SOV_PERIOD", "source_id": "2026-08"},
        result_summary={"measurement_month": "2026-08"},
    )

    identity = task_incident_control._terminal_outcome_identity(run)

    assert identity is not None
    assert identity.cause == "PERIOD_FINALIZATION_FAILED"
    assert identity.period == "2026-08"
    run.safe_error_code = "PERIOD_INPUT_INCOMPLETE"
    other_cause = task_incident_control._terminal_outcome_identity(run)
    assert other_cause is not None
    assert other_cause.dedupe_key != identity.dedupe_key


def test_every_signalled_operation_has_an_explicit_domain_identity() -> None:
    assert set(task_incident_control._DOMAIN_OUTCOME_NAMES) == set(
        task_incident_control._SIGNALLED_DOMAIN_OPERATIONS
    )
    assert set(task_incident_control._CLASSIFIED_DOMAIN_OWNERS) < set(
        task_incident_control._SIGNALLED_DOMAIN_OPERATIONS
    )
    assert "MONTHLY_SOV_PERIOD" not in task_incident_control._CLASSIFIED_DOMAIN_OWNERS


def test_equivalent_invocation_paths_share_domain_identity() -> None:
    hospital_id = uuid.uuid4()
    content_id = uuid.uuid4()

    def content_run(operation_type: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            operation_type=operation_type,
            safe_error_code="TASK_FAILED",
            idempotency_key=None,
            request_payload={
                "revision": 7,
                "_dispatch": {
                    "target_type": "content_item",
                    "target_id": str(content_id),
                    "task_args": [str(content_id)],
                },
            },
            result_summary=None,
        )

    automatic = task_incident_control._terminal_outcome_identity(
        content_run("GENERATE_CONTENT_ITEM")
    )
    operator_retry = task_incident_control._terminal_outcome_identity(
        content_run("REGENERATE_CONTENT")
    )

    assert automatic is not None and operator_retry is not None
    assert automatic.dedupe_key == operator_retry.dedupe_key
    assert automatic.source_id == operator_retry.source_id

    changed_revision = content_run("REGENERATE_CONTENT")
    changed_revision.request_payload["revision"] = 8
    next_identity = task_incident_control._terminal_outcome_identity(changed_revision)
    assert next_identity is not None
    assert next_identity.dedupe_key != automatic.dedupe_key

    different_cause = content_run("REGENERATE_CONTENT")
    different_cause.safe_error_code = "TASK_FAILED_AFTER_TIMEOUT"
    # This operation owns classified errors, so the task body is authoritative.
    assert task_incident_control._terminal_outcome_identity(different_cause) is None


def test_scheduled_and_manual_monthly_report_share_period_outcome() -> None:
    hospital_id = uuid.uuid4()

    def report_run(operation_type: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            operation_type=operation_type,
            safe_error_code="TASK_FAILED",
            idempotency_key=f"report:{hospital_id}:2026-08",
            request_payload={
                "source_type": "MONTHLY_REPORT",
                "source_id": "2026-08",
                "_dispatch": {
                    "target_type": "hospital",
                    "target_id": str(hospital_id),
                    "task_args": [str(hospital_id), 2026, 8],
                },
            },
            result_summary={"period_year": 2026, "period_month": 8},
        )

    scheduled = task_incident_control._terminal_outcome_identity(
        report_run("SCHEDULED_MONTHLY_REPORT")
    )
    manual = task_incident_control._terminal_outcome_identity(
        report_run("GENERATE_MONTHLY_REPORT")
    )

    assert scheduled is not None and manual is not None
    assert scheduled.dedupe_key == manual.dedupe_key
    assert scheduled.period == manual.period == "2026-08"


def test_runtime_batch_header_supplies_failure_correlation() -> None:
    run_id = uuid.uuid4()
    task = SimpleNamespace(
        request=SimpleNamespace(
            headers={"reputation_dispatch_operation_run_id": str(run_id)}
        )
    )

    assert task_incident_control._run_identity(task, " monthly-batch-task ") == (
        run_id,
        "monthly-batch-task",
    )


@pytest.mark.asyncio
async def test_exact_run_failure_opens_then_same_run_success_recovers(
    signal_store,
    monkeypatch,
) -> None:
    factory, hospital_id = signal_store
    dispatched = RecordingTask()
    # generic 경로의 계약을 본다 — sweep이 주인인 REBUILD_SITE는 시도마다 사고를
    # 열지 않으므로(H-13) 여기서는 일반 작업 유형을 쓴다.
    run = await dispatch_test_run(
        factory, hospital_id, dispatched, "task20-recovery", "TRIGGER_V0_REPORT"
    )
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
        lambda _db, _incident, action, **_kwargs: audit_actions.append(action),
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
                for label in ("백그라운드 작업 중단", "개발 담당자", "지금 할 일")
            )
            assert "작업 오류 보기" in rendered
            assert "참조 OPS-" in rendered
            assert "private@example.com" not in rendered

        assert task_incident_control.record_task_success(celery_task, "unrelated-task") is False
        with sync_factory() as db:
            still_open = db.get(Incident, incident_ids[0])
            assert still_open is not None and still_open.state == "OPEN"

        assert task_incident_control.record_task_success(celery_task, run.task_id) is True
        with sync_factory() as db:
            recovered = db.get(Incident, incident_ids[0])
            assert recovered is not None
            # The machine opened, retried, recovered and closed this without a person.
            assert recovered.state == "ACKNOWLEDGED"
            assert recovered.recovered_at is not None
            assert recovered.acknowledged_at is not None
            assert recovered.acknowledged_by_id is None
            notices = list(
                db.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.incident_id == recovered.id
                    )
                )
            )
            # The OPEN notice went out, so its RECOVERED must follow — a Slack pair is
            # suppressed only as a pair, never half of it. Suppressing just the
            # recovery left "운영 확인 필요" standing in the channel with nothing to
            # close it.
            assert sorted(notice.notification_type for notice in notices) == [
                "INCIDENT_OPEN",
                "INCIDENT_RECOVERED",
            ]
            assert audit_actions[-3:] == [
                "incident_retrying",
                "incident_recovered",
                "incident_auto_acknowledged",
            ]
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


@pytest.mark.asyncio
async def test_recovery_stays_silent_when_the_open_notice_never_reached_the_outbox(
    signal_store,
    monkeypatch,
) -> None:
    """자동 복구 억제의 유일한 근거는 "OPEN이 나갔는가"다.

    분류된 파이프라인(예: RUN_SOV)이 자기 인시던트를 이미 냈거나 `notify=False`로 연
    건은 OPEN 공지가 outbox에 없다. 그런 건의 복구는 Slack이 아니라 DB 인시던트와
    감사 로그로만 남아야 한다.
    """
    factory, hospital_id = signal_store
    run = await dispatch_test_run(
        factory, hospital_id, RecordingTask(), "task20-silent", "TRIGGER_V0_REPORT"
    )
    celery_task = SimpleNamespace(
        request=SimpleNamespace(headers={"operation_run_id": str(run.id)})
    )
    sync_engine = create_engine(SYNC_DATABASE_URL)
    sync_factory = sessionmaker(sync_engine, expire_on_commit=False, class_=Session)
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", sync_factory)
    monkeypatch.setattr(task_incident_control, "_audit", lambda *_args, **_kwargs: None)
    incident_ids: list[uuid.UUID] = []
    try:
        assert task_incident_control.record_task_failure(celery_task, run.task_id) is True
        with sync_factory() as db:
            incident = db.scalar(select(Incident).where(Incident.operation_run_id == run.id))
            assert incident is not None
            incident_ids.append(incident.id)
            # 이 인시던트의 OPEN 공지는 채널에 도달하지 않았다.
            db.execute(
                delete(NotificationOutbox).where(
                    NotificationOutbox.incident_id == incident.id,
                    NotificationOutbox.notification_type == "INCIDENT_OPEN",
                )
            )
            db.commit()

        assert task_incident_control.record_task_success(celery_task, run.task_id) is True

        with sync_factory() as db:
            recovered = db.get(Incident, incident_ids[0])
            assert recovered is not None and recovered.state == "ACKNOWLEDGED"
            assert (
                db.scalar(
                    select(func.count(NotificationOutbox.id)).where(
                        NotificationOutbox.incident_id == incident_ids[0]
                    )
                )
                == 0
            )
    finally:
        with sync_factory() as db:
            if incident_ids:
                db.execute(
                    delete(NotificationOutbox).where(
                        NotificationOutbox.incident_id.in_(incident_ids)
                    )
                )
                db.execute(delete(Incident).where(Incident.id.in_(incident_ids)))
                db.commit()
        sync_engine.dispose()


@pytest.mark.asyncio
async def test_generic_task_failure_assigns_the_handoff_ae_and_says_so_in_slack(
    signal_store,
    monkeypatch,
) -> None:
    """generic Celery 실패로 열린 예외도 담당자를 받는다 (H-15).

    서비스 경로에서만 배정하면 이 경로로 열린 예외는 언제나 주인이 없고, Slack도
    "미지정"으로 알린다 — 아무도 자기 일로 보지 않는다.
    """
    factory, hospital_id = signal_store
    run = await dispatch_test_run(
        factory, hospital_id, RecordingTask(), "task20-assign", "TRIGGER_V0_REPORT"
    )
    celery_task = SimpleNamespace(
        request=SimpleNamespace(headers={"operation_run_id": str(run.id)})
    )
    sync_engine = create_engine(SYNC_DATABASE_URL)
    sync_factory = sessionmaker(sync_engine, expire_on_commit=False, class_=Session)
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", sync_factory)
    incident_ids: list[uuid.UUID] = []
    admin_ids: list[uuid.UUID] = []
    handoff_ids: list[uuid.UUID] = []
    audits: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        task_incident_control,
        "_audit",
        lambda _db, _incident, action, **kwargs: audits.append(
            (action, dict(kwargs.get("detail_extra") or {}))
        ),
    )
    try:
        with sync_factory() as db:
            ae = AdminUser(
                email=f"task20-ae-{run.id}@example.test",
                name="배정 대상 AE",
                password_hash="not-a-real-password-hash",
            )
            db.add(ae)
            db.flush()
            admin_ids.append(ae.id)
            handoff = HospitalHandoff.pending(
                hospital_id, sales_owner_id=ae.id, ae_owner_id=ae.id
            )
            db.add(handoff)
            db.flush()
            handoff_ids.append(handoff.id)
            db.commit()
            ae_id, ae_name = ae.id, ae.name

        assert task_incident_control.record_task_failure(celery_task, run.task_id) is True

        with sync_factory() as db:
            incident = db.scalar(select(Incident).where(Incident.operation_run_id == run.id))
            assert incident is not None
            incident_ids.append(incident.id)
            assert incident.owner_id == ae_id
            notice = db.scalar(
                select(NotificationOutbox).where(
                    NotificationOutbox.incident_id == incident.id,
                    NotificationOutbox.notification_type == "INCIDENT_OPEN",
                )
            )
            assert notice is not None
            rendered = json.dumps(notice.payload, ensure_ascii=False)
            assert f"담당: {ae_name}" in rendered
            assert "미지정" not in rendered
        assert ("incident_assigned", {"auto_assigned": True, "auto_assigned_to": str(ae_id)}) in (
            audits
        )
    finally:
        with sync_factory() as db:
            if incident_ids:
                db.execute(
                    delete(NotificationOutbox).where(
                        NotificationOutbox.incident_id.in_(incident_ids)
                    )
                )
                db.execute(delete(Incident).where(Incident.id.in_(incident_ids)))
            if handoff_ids:
                db.execute(
                    delete(HospitalHandoff).where(HospitalHandoff.id.in_(handoff_ids))
                )
            if admin_ids:
                db.execute(delete(AdminUser).where(AdminUser.id.in_(admin_ids)))
            db.commit()
        sync_engine.dispose()
