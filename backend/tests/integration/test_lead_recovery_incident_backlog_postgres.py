"""리드 진단 복구 사고(RECOVER_LEAD_*)를 진단 행의 근거로 닫고, 근거가 없으면 열어 둔다.

병원으로 전환되지 않은 리드의 복구 사고는 같은 진단의 다음 복구 성공(RETRYING에서만 닫힘)만
기다렸다. 그 복구가 다시 오지 않으면 사고는 영영 OPEN이다(2026-09-22분, 10/4 보고).
`close_resolved_backlog_incidents`(#203)와 같은 규칙으로 닫는다 — OPEN만, Slack 없이 감사만,
RETRYING·ACKNOWLEDGED는 건드리지 않는다. 근거는 그 사고가 가리키는 **그 진단**의 현재 상태다.

- 측정 사고: 진단 실행이 SUCCEEDED/PARTIAL(`REPORTABLE_EXECUTION_STATUSES`)이다.
- 리포트 사고: 리포트가 READY이고 파기되지 않은 산출물이 있다.
- 두 축 모두: 진단이 갈음됐다(`superseded_at`) · 파기됐다(`report_status=PURGED`) · 행이 없다.
  복구 claim(HTTP·워커)이 셋 모두를 거절하므로 누구도 이 사고를 처리할 수 없다.

사고 행은 실제 워커 경로(`mark_lead_recovery_failed`)로 연다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.models.audit import AdminAuditLog
from app.models.lead import SalesLead
from app.models.lead_diagnosis import LeadDiagnosis, LeadReportArtifact
from app.models.operations import Incident, NotificationOutbox
from app.workers import lead_recovery_incidents
from app.workers.incident_backlog import close_resolved_backlog_incidents

CLOSE_ACTION = "incident_resolved_by_later_evidence"


@pytest.fixture
def worker_session(pg_async_session, monkeypatch):
    """The recovery task's own session is the test transaction (rolled back afterwards)."""

    class _Ctx:
        async def __aenter__(self):
            return pg_async_session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(lead_recovery_incidents, "get_async_sessionmaker", lambda: _Ctx)
    return pg_async_session


async def _lead(db) -> SalesLead:
    # 도입문의 리드: 병원으로 전환되지 않았다(converted_hospital_id 없음).
    lead = SalesLead(
        clinic_name="복구사고가상의원",
        clinic_type="도입문의",
        contact="010-0000-0000",
        privacy=False,
        source="INQUIRY",
    )
    db.add(lead)
    await db.flush()
    return lead


async def _diagnosis(db, lead=None, **status) -> LeadDiagnosis:
    lead = lead or await _lead(db)
    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        subject_hospital_name="복구사고가상의원",
        subject_region="수서역",
        queries=[{"slot": 1, "kind": "진료과형", "text": "수서역 근처 내과 병원 추천해줘"}],
        requested_models={"openai": "m", "gemini": "g", "judge": "j"},
        repeat_count=3,
        **status,
    )
    db.add(diagnosis)
    await db.flush()
    return diagnosis


async def _artifact(db, diagnosis, *, purged=False):
    db.add(
        LeadReportArtifact(
            diagnosis_id=diagnosis.id,
            version=1,
            storage_uri=f"gs://test-bucket/lead-reports/{diagnosis.id}.pdf",
            content_hash="0" * 64,
            byte_size=1024,
            template_version="lead-v1",
            purged_at=datetime.now(UTC) if purged else None,
        )
    )
    await db.flush()


async def _failed_recovery(db, diagnosis_id, axis) -> Incident:
    """The worker's terminal recovery failure (LeadRecoveryRejected → OPEN incident)."""
    await lead_recovery_incidents.mark_lead_recovery_failed(
        diagnosis_id, axis, None, "measurement recovery state changed"
    )
    incident = await db.scalar(
        select(Incident).where(
            Incident.source_id == str(diagnosis_id),
            Incident.incident_type == f"RECOVER_LEAD_{axis}",
        )
    )
    assert incident is not None and incident.state == "OPEN"
    assert incident.incident_type == f"RECOVER_LEAD_{axis}"
    assert incident.hospital_id is None
    return incident


async def _set_state(db, incident, state):
    # ACKNOWLEDGED는 복구·확인 시각을 함께 가져야 한다(ck_incidents_*_fact).
    closed = state == "ACKNOWLEDGED"
    await db.execute(
        text(
            "UPDATE incidents SET state = :state, recovered_at = :at, acknowledged_at = :at "
            "WHERE id = :id"
        ),
        {"state": state, "at": datetime.now(UTC) if closed else None, "id": incident.id},
    )


async def _outbox_count(db) -> int:
    return int(await db.scalar(select(func.count()).select_from(NotificationOutbox)))


async def _sweep(db) -> int:
    return await db.run_sync(lambda session: close_resolved_backlog_incidents(session))


async def _state(db, incident) -> str:
    return await db.scalar(select(Incident.state).where(Incident.id == incident.id))


async def _close_audits(db, incident) -> list[dict]:
    rows = await db.scalars(
        select(AdminAuditLog.detail).where(
            AdminAuditLog.target_type == "incident",
            AdminAuditLog.target_id == str(incident.id),
            AdminAuditLog.action == CLOSE_ACTION,
        )
    )
    return list(rows)


async def _assert_closed_quietly(db, incident, evidence, outbox_before):
    assert await _state(db, incident) == "ACKNOWLEDGED"
    audits = await _close_audits(db, incident)
    assert len(audits) == 1
    assert audits[0]["evidence"] == evidence
    assert audits[0]["slack_suppressed"] is True
    # 지나간 일의 정리다 — 복구 알림을 채널에 보내지 않는다.
    assert await _outbox_count(db) == outbox_before


async def _assert_left_alone(db, incident, expected_state="OPEN"):
    assert await _state(db, incident) == expected_state
    assert await _close_audits(db, incident) == []


# ── 닫힘: 그 진단에 복구가 더는 필요 없다는 근거 ────────────────────────────────────


@pytest.mark.parametrize("execution_status", ["SUCCEEDED", "PARTIAL"])
async def test_measurement_incident_closes_once_that_diagnosis_has_a_usable_measurement(
    worker_session, execution_status
):
    db = worker_session
    diagnosis = await _diagnosis(db, execution_status="FAILED", execution_attempts=2)
    incident = await _failed_recovery(db, diagnosis.id, "MEASUREMENT")
    # 폴러나 다른 경로가 나중에 같은 진단의 측정을 끝냈다.
    diagnosis.execution_status = execution_status
    await db.flush()
    outbox_before = await _outbox_count(db)

    assert await _sweep(db) >= 1

    await _assert_closed_quietly(db, incident, "lead_measurement_succeeded", outbox_before)


async def test_report_incident_closes_once_that_diagnosis_has_a_servable_report(worker_session):
    db = worker_session
    diagnosis = await _diagnosis(
        db, execution_status="SUCCEEDED", report_status="BLOCKED", report_attempts=1
    )
    incident = await _failed_recovery(db, diagnosis.id, "REPORT")
    diagnosis.report_status = "READY"
    await db.flush()
    await _artifact(db, diagnosis)
    outbox_before = await _outbox_count(db)

    await _sweep(db)

    await _assert_closed_quietly(db, incident, "lead_report_ready", outbox_before)


@pytest.mark.parametrize("axis", ["MEASUREMENT", "REPORT"])
async def test_incident_opened_after_the_diagnosis_was_superseded_closes(worker_session, axis):
    db = worker_session
    lead = await _lead(db)
    old = await _diagnosis(
        db,
        lead,
        execution_status="FAILED" if axis == "MEASUREMENT" else "SUCCEEDED",
        report_status="PENDING" if axis == "MEASUREMENT" else "BLOCKED",
    )
    # 값 고쳐 다시 만들기: 옛 진단을 갈음한다. 갈음 경로는 그 순간 열려 있던 사고만 닫는다.
    old.superseded_at = datetime.now(UTC) - timedelta(minutes=5)
    await db.flush()
    replacement = await _diagnosis(db, lead, execution_status="PENDING")
    old.superseded_by_id = replacement.id
    await db.flush()
    # 이미 큐에 있던 복구 작업이 갈음 뒤에 claim을 잃고 사고를 다시 연다.
    incident = await _failed_recovery(db, old.id, axis)
    outbox_before = await _outbox_count(db)

    await _sweep(db)

    await _assert_closed_quietly(db, incident, "lead_diagnosis_superseded", outbox_before)
    lead_row = await db.get(SalesLead, lead.id)
    assert lead_row.converted_hospital_id is None


@pytest.mark.parametrize("axis", ["MEASUREMENT", "REPORT"])
async def test_incident_on_a_purged_diagnosis_closes(worker_session, axis):
    db = worker_session
    diagnosis = await _diagnosis(
        db,
        execution_status="FAILED" if axis == "MEASUREMENT" else "SUCCEEDED",
        report_status="BLOCKED" if axis == "REPORT" else "PENDING",
    )
    incident = await _failed_recovery(db, diagnosis.id, axis)
    # 개인정보 파기(180일·요청): 리포트와 질의 원문이 지워지고 복구 claim이 거절된다.
    diagnosis.report_status = "PURGED"
    await db.flush()
    outbox_before = await _outbox_count(db)

    await _sweep(db)

    await _assert_closed_quietly(db, incident, "lead_diagnosis_purged", outbox_before)


async def test_incident_whose_diagnosis_row_no_longer_exists_closes(worker_session):
    db = worker_session
    diagnosis = await _diagnosis(db, execution_status="FAILED")
    incident = await _failed_recovery(db, diagnosis.id, "MEASUREMENT")
    await db.execute(text("DELETE FROM lead_diagnoses WHERE id = :id"), {"id": diagnosis.id})
    outbox_before = await _outbox_count(db)

    await _sweep(db)

    await _assert_closed_quietly(db, incident, "lead_diagnosis_missing", outbox_before)


# ── 열어 둠: 근거가 없거나 자동화·사람의 것 ──────────────────────────────────────────


async def test_a_still_failed_active_measurement_stays_open_however_old(worker_session):
    db = worker_session
    diagnosis = await _diagnosis(db, execution_status="FAILED", execution_attempts=3)
    incident = await _failed_recovery(db, diagnosis.id, "MEASUREMENT")
    # 나이만으로는 닫지 않는다.
    await db.execute(
        text(
            "UPDATE incidents SET first_seen_at = :at, last_seen_at = :at WHERE id = :id"
        ),
        {"at": datetime.now(UTC) - timedelta(days=60), "id": incident.id},
    )

    await _sweep(db)

    await _assert_left_alone(db, incident)


async def test_a_blocked_report_stays_open_even_though_its_measurement_succeeded(worker_session):
    db = worker_session
    diagnosis = await _diagnosis(
        db, execution_status="SUCCEEDED", report_status="BLOCKED", report_attempts=2
    )
    report_incident = await _failed_recovery(db, diagnosis.id, "REPORT")

    await _sweep(db)

    # 측정 성공은 리포트 축의 근거가 아니다.
    await _assert_left_alone(db, report_incident)


async def test_ready_without_a_servable_artifact_is_not_report_evidence(worker_session):
    db = worker_session
    diagnosis = await _diagnosis(
        db, execution_status="SUCCEEDED", report_status="BLOCKED", report_attempts=1
    )
    incident = await _failed_recovery(db, diagnosis.id, "REPORT")
    diagnosis.report_status = "READY"
    await db.flush()
    await _artifact(db, diagnosis, purged=True)

    await _sweep(db)

    await _assert_left_alone(db, incident)


async def test_another_diagnosis_success_does_not_close_this_one(worker_session):
    db = worker_session
    failed = await _diagnosis(db, execution_status="FAILED")
    incident = await _failed_recovery(db, failed.id, "MEASUREMENT")
    other = await _diagnosis(db, execution_status="SUCCEEDED", report_status="READY")
    await _artifact(db, other)
    # 같은 리드의 다른 진단도 갈음이 아니면 근거가 아니다.
    sibling = await _diagnosis(
        db, await db.get(SalesLead, failed.lead_id), execution_status="SUCCEEDED"
    )
    assert sibling.superseded_at is None and failed.superseded_at is None

    await _sweep(db)

    await _assert_left_alone(db, incident)


@pytest.mark.parametrize("state", ["RETRYING", "ACKNOWLEDGED"])
async def test_retrying_and_acknowledged_incidents_are_not_touched(worker_session, state):
    db = worker_session
    diagnosis = await _diagnosis(db, execution_status="FAILED")
    incident = await _failed_recovery(db, diagnosis.id, "MEASUREMENT")
    await _set_state(db, incident, state)
    # 근거가 있어도(갈음) 자동화나 사람이 가진 사고는 이 정리의 몫이 아니다.
    diagnosis.superseded_at = datetime.now(UTC)
    await db.flush()

    await _sweep(db)

    await _assert_left_alone(db, incident, expected_state=state)


async def test_lead_recovery_closing_does_not_disturb_existing_backlog_kinds(worker_session):
    """A RECOVER_LEAD sweep shares the batch with #203 kinds without starving them."""
    db = worker_session
    budget = Incident(
        dedupe_key=f"backlog-test:{uuid.uuid4()}",
        incident_type="COST_GUARD_LIMIT_REACHED",
        state="OPEN",
        severity="HIGH",
        customer_impact="테스트",
        source_type="TEST",
        source_id="sov:daily:20260817:hard",
        next_action="테스트",
        admin_path="/operations",
        first_seen_at=datetime(2026, 8, 17, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 17, tzinfo=UTC),
    )
    db.add(budget)
    still = await _diagnosis(db, execution_status="FAILED")
    open_incident = await _failed_recovery(db, still.id, "MEASUREMENT")
    await db.flush()

    await _sweep(db)

    assert await _state(db, budget) == "ACKNOWLEDGED"
    await _assert_left_alone(db, open_incident)
