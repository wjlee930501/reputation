"""이미 해결된 일을 가리키는 generic 작업 실패 사고를 닫는다(2026-10-02).

사고는 실행 하나에 묶여 그 실행의 성공만 닫았다. 다음 주 측정·다음 생성 같은 새 실행이 같은
일을 끝내도 옛 사고가 열려 일일 요약의 '백그라운드 작업 중단'으로 쌓였다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.workers.task_incident_control import close_resolved_task_incidents

NOW = datetime.now(UTC)


@pytest.fixture
def db(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _hospital(db) -> Hospital:
    hospital = Hospital(
        name="해결 사고 정리 가상의원",
        slug=f"resolved-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
    )
    db.add(hospital)
    db.flush()
    return hospital


def _run(db, hospital, operation_type, target_type, target_id, state, *, at) -> OperationRun:
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type=operation_type,
        state=state,
        request_payload=build_request_payload(
            DispatchPayload(target_type, str(target_id), "default", (str(target_id),))
        ),
        requested_at=at,
    )
    db.add(run)
    db.flush()
    return run


def _incident(db, hospital, run) -> Incident:
    incident = Incident(
        hospital_id=hospital.id,
        operation_run_id=run.id,
        dedupe_key=f"worker_task:{run.id}",
        incident_type="BACKGROUND_TASK_FAILED",
        state="OPEN",
        severity="HIGH",
        customer_impact="자동 작업이 완료되지 않았습니다.",
        source_type="OPERATION_RUN",
        source_id=str(run.id),
        safe_error_code="TASK_FAILED",
        next_action="작업 오류를 확인해 주세요.",
        admin_path="/operations",
    )
    db.add(incident)
    db.flush()
    return incident


def _state(db, incident) -> str:
    db.expire_all()
    return db.get(Incident, incident.id).state


def test_a_later_success_on_the_same_target_closes_the_old_failure(db):
    hospital = _hospital(db)
    failed = _run(db, hospital, "RUN_SOV", "hospital", hospital.id, "FAILED", at=NOW - timedelta(days=30))
    incident = _incident(db, hospital, failed)
    _run(db, hospital, "RUN_SOV", "hospital", hospital.id, "SUCCEEDED", at=NOW - timedelta(days=23))

    assert close_resolved_task_incidents(db) >= 1

    assert _state(db, incident) == "ACKNOWLEDGED"
    audit = db.scalar(
        select(AdminAuditLog).where(
            AdminAuditLog.target_id == str(incident.id),
            AdminAuditLog.action == "incident_recovered_by_later_success",
        )
    )
    assert audit is not None and audit.detail["slack_suppressed"] is True
    # 지난 일을 정리하는 것이라 복구 Slack을 쌓지 않는다.
    assert db.scalar(
        select(NotificationOutbox).where(NotificationOutbox.incident_id == incident.id)
    ) is None


def test_a_run_that_eventually_succeeded_closes_its_own_failure(db):
    hospital = _hospital(db)
    run = _run(db, hospital, "TRIGGER_V0_REPORT", "hospital", hospital.id, "SUCCEEDED", at=NOW)
    incident = _incident(db, hospital, run)

    close_resolved_task_incidents(db)

    assert _state(db, incident) == "ACKNOWLEDGED"


def test_a_published_content_item_closes_its_generation_failure(db):
    hospital = _hospital(db)
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[1], active_from=date(2026, 9, 1)
    )
    db.add(schedule)
    db.flush()
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        scheduled_date=date(2026, 9, 30),
        title="가상 안내 글",
        status=ContentStatus.PUBLISHED,
        first_published_at=NOW - timedelta(days=1),
    )
    db.add(item)
    db.flush()
    failed = _run(
        db, hospital, "GENERATE_CONTENT_ITEM", "content_item", item.id, "FAILED",
        at=NOW - timedelta(days=2),
    )
    incident = _incident(db, hospital, failed)

    close_resolved_task_incidents(db)

    assert _state(db, incident) == "ACKNOWLEDGED"


def test_an_unresolved_failure_stays_open(db):
    hospital = _hospital(db)
    failed = _run(db, hospital, "RUN_SOV", "hospital", hospital.id, "FAILED", at=NOW - timedelta(days=1))
    incident = _incident(db, hospital, failed)
    # 다른 종류의 성공, 다른 대상의 성공, 더 이른 성공은 해결 근거가 아니다.
    _run(db, hospital, "TRIGGER_V0_REPORT", "hospital", hospital.id, "SUCCEEDED", at=NOW)
    _run(db, hospital, "RUN_SOV", "hospital", uuid.uuid4(), "SUCCEEDED", at=NOW)
    _run(db, hospital, "RUN_SOV", "hospital", hospital.id, "SUCCEEDED", at=NOW - timedelta(days=5))

    close_resolved_task_incidents(db)

    assert _state(db, incident) == "OPEN"
