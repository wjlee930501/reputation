"""영구 OPEN이던 사고 종류를 DB 근거로 닫고, 근거가 없으면 열어 둔다(Phase 1-2)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.lead import SalesLead
from app.models.lead_diagnosis import LeadDelivery, LeadDiagnosis, LeadReportArtifact
from app.models.operations import Incident, OperationRun
from app.workers.incident_backlog import close_resolved_backlog_incidents

NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _hospital(db, **fields) -> Hospital:
    hospital = Hospital(
        name="사고해결가상의원",
        slug=f"resolver-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
        **fields,
    )
    db.add(hospital)
    db.flush()
    return hospital


def _incident(
    db, incident_type, *, hospital=None, source_type="TEST", source_id=None,
    state="OPEN", seen=NOW - timedelta(days=30), run=None, sla=None,
):
    incident = Incident(
        hospital_id=hospital.id if hospital else None,
        operation_run_id=run.id if run else None,
        dedupe_key=f"resolver-test:{uuid.uuid4()}",
        incident_type=incident_type,
        state=state,
        severity="HIGH",
        customer_impact="테스트",
        source_type=source_type,
        source_id=source_id,
        next_action="테스트",
        admin_path="/operations",
        sla_due_at=sla,
        first_seen_at=seen,
        last_seen_at=seen,
    )
    db.add(incident)
    db.flush()
    return incident


def _state(db, incident) -> str:
    db.expire_all()
    return db.get(Incident, incident.id).state


def _diagnosis(db, **status):
    lead = SalesLead(
        clinic_name="사고해결가상의원", clinic_type="도입문의", contact="010-0000-0000",
        privacy=False, source="INQUIRY",
    )
    db.add(lead)
    db.flush()
    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        subject_hospital_name="사고해결가상의원",
        subject_region="수서역",
        queries=[{"slot": 1, "kind": "진료과형", "text": "수서역 근처 내과 병원 추천해줘"}],
        requested_models={"openai": "m", "gemini": "g", "judge": "j"},
        repeat_count=3,
        **status,
    )
    db.add(diagnosis)
    db.flush()
    return lead, diagnosis


@pytest.mark.parametrize("kind", ["LEAD_DIAGNOSIS_FAILED", "LEAD_DIAGNOSIS_RETRIES_EXHAUSTED"])
def test_lead_measurement_failure_closes_once_that_diagnosis_measured(db, kind):
    _lead, done = _diagnosis(db, execution_status="SUCCEEDED")
    _lead2, still = _diagnosis(db, execution_status="FAILED")
    closable = _incident(db, kind, source_type="LEAD_DIAGNOSIS", source_id=str(done.id))
    open_one = _incident(db, kind, source_type="LEAD_DIAGNOSIS", source_id=str(still.id))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, closable) == "ACKNOWLEDGED"
    assert _state(db, open_one) == "OPEN"


def test_lead_report_block_closes_once_a_servable_report_is_built_after_it(db):
    _lead, diagnosis = _diagnosis(db, execution_status="SUCCEEDED", report_status="READY")
    incident = _incident(
        db, "LEAD_REPORT_BLOCKED", source_type="LEAD_REPORT", source_id=str(diagnosis.id)
    )
    db.add(
        LeadReportArtifact(
            diagnosis_id=diagnosis.id, version=1, created_at=NOW - timedelta(days=1),
            storage_uri=f"gs://test-bucket/lead-reports/{diagnosis.id}.pdf",
            content_hash="0" * 64, byte_size=1024, template_version="lead-v1",
        )
    )
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"


def test_abandoned_delivery_closes_once_that_delivery_was_sent_again(db):
    # 수동 재발송은 같은 행을 다시 쓴다(진단·이벤트·채널 유일 제약).
    lead, diagnosis = _diagnosis(db, execution_status="SUCCEEDED", report_status="READY")
    resent = LeadDelivery(lead_id=lead.id, diagnosis_id=diagnosis.id, event="REPORT", status="FAILED")
    db.add(resent)
    _lead2, diagnosis2 = _diagnosis(db, execution_status="SUCCEEDED", report_status="READY")
    still_failed = LeadDelivery(
        lead_id=diagnosis2.lead_id, diagnosis_id=diagnosis2.id, event="REPORT", status="FAILED"
    )
    db.add(still_failed)
    db.flush()
    closable = _incident(db, "LEAD_DELIVERY_ABANDONED", source_type="LEAD_DELIVERY", source_id=str(resent.id))
    still = _incident(db, "LEAD_DELIVERY_ABANDONED", source_type="LEAD_DELIVERY", source_id=str(still_failed.id))
    resent.status = "SENT"
    resent.sent_at = NOW - timedelta(days=1)
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, closable) == "ACKNOWLEDGED"
    assert _state(db, still) == "OPEN"


def test_must_use_exclusion_closes_once_that_version_is_no_longer_approved(db):
    replaced = _hospital(db)
    current = _hospital(db)
    old = HospitalContentPhilosophy(hospital_id=replaced.id, version=1, status=PhilosophyStatus.ARCHIVED)
    live = HospitalContentPhilosophy(hospital_id=current.id, version=1, status=PhilosophyStatus.APPROVED)
    db.add_all([old, live])
    db.flush()
    closable = _incident(db, "ESSENCE_MUST_USE_EXCLUDED", hospital=replaced, source_id=str(old.id))
    still = _incident(db, "ESSENCE_MUST_USE_EXCLUDED", hospital=current, source_id=str(live.id))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, closable) == "ACKNOWLEDGED"
    assert _state(db, still) == "OPEN"


def test_naver_failure_closes_once_a_later_run_stored_that_post(db):
    hospital = _hospital(db)
    url_hash = "a" * 64
    incident = _incident(db, "NAVER_SOURCE_FETCH_FAILED", hospital=hospital, source_id=url_hash)
    # 다른 글을 저장한 실행은 이 글의 근거가 아니다.
    other = _incident(db, "NAVER_SOURCE_FETCH_FAILED", hospital=hospital, source_id="b" * 64)
    db.add(
        OperationRun(
            hospital_id=hospital.id, operation_type="NAVER_SOURCE_SYNC", state="SUCCEEDED",
            request_payload={"source_type": "NAVER_BLOG", "url_hashes": [url_hash]},
            result_summary={"items": [{"url_hash": url_hash, "state": "INGESTED"}]},
            requested_at=NOW - timedelta(days=1),
        )
    )
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"
    assert _state(db, other) == "OPEN"


def test_broker_failure_closes_once_the_same_work_reached_the_queue(db):
    hospital = _hospital(db)
    failed = OperationRun(
        hospital_id=hospital.id, operation_type="REBUILD_SITE", state="FAILED",
        request_payload={}, requested_at=NOW - timedelta(days=30), safe_error_code="BROKER_UNAVAILABLE",
    )
    db.add(failed)
    db.flush()
    incident = _incident(db, "BROKER_UNAVAILABLE", hospital=hospital, run=failed, source_id=str(failed.id))
    # 다른 병원의 같은 종류 실행은 근거가 아니다.
    elsewhere = _hospital(db)
    failed_elsewhere = OperationRun(
        hospital_id=elsewhere.id, operation_type="REBUILD_SITE", state="FAILED",
        request_payload={}, requested_at=NOW - timedelta(days=30), safe_error_code="BROKER_UNAVAILABLE",
    )
    db.add(failed_elsewhere)
    db.flush()
    still = _incident(
        db, "BROKER_UNAVAILABLE", hospital=elsewhere, run=failed_elsewhere, source_id=str(failed_elsewhere.id)
    )
    db.add(
        OperationRun(
            hospital_id=hospital.id, operation_type="REBUILD_SITE", state="QUEUED",
            request_payload={}, requested_at=NOW - timedelta(days=1), queued_at=NOW - timedelta(days=1),
        )
    )
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"
    assert _state(db, still) == "OPEN"


def test_site_dispatch_failure_closes_once_the_site_is_built_even_while_retrying(db):
    built = _hospital(db, site_built=True)
    waiting = _hospital(db, site_built=False)
    closable = _incident(
        db, "SITE_BUILD_DISPATCH_FAILED", hospital=built, state="RETRYING", sla=NOW + timedelta(hours=5)
    )
    still = _incident(
        db, "SITE_BUILD_DISPATCH_FAILED", hospital=waiting, state="RETRYING", sla=NOW + timedelta(hours=5)
    )

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, closable) == "ACKNOWLEDGED"
    assert _state(db, still) == "RETRYING"


def test_content_dispatch_failure_waits_for_near_slots_then_hands_over_after_a_day(db):
    hospital = _hospital(db)
    schedule = ContentSchedule(hospital_id=hospital.id, plan="PLAN_12", publish_days=[1], active_from=date(2026, 10, 1))
    db.add(schedule)
    db.flush()
    db.add(
        ContentItem(
            hospital_id=hospital.id, schedule_id=schedule.id, content_type=ContentType.DISEASE,
            sequence_no=1, total_count=12, scheduled_date=date(2026, 10, 2), status=ContentStatus.DRAFT,
        )
    )
    db.flush()
    fresh = _incident(db, "CONTENT_DISPATCH_FAILED", hospital=hospital, state="RETRYING",
                      seen=NOW - timedelta(hours=2), sla=NOW + timedelta(hours=22))
    stale = _incident(db, "CONTENT_DISPATCH_FAILED", hospital=hospital, state="RETRYING",
                      seen=NOW - timedelta(hours=25), sla=NOW - timedelta(hours=1))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, fresh) == "RETRYING"
    assert _state(db, stale) == "ACKNOWLEDGED"


def test_cohort_over_limit_closes_when_its_month_ends(db):
    past = _incident(db, "MONTHLY_SOV_COHORT_OVER_LIMIT", source_id="2026-09")
    current = _incident(db, "MONTHLY_SOV_COHORT_OVER_LIMIT", source_id="2026-10")

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, past) == "ACKNOWLEDGED"
    assert _state(db, current) == "OPEN"


def test_unverified_actor_closes_after_thirty_quiet_days(db):
    quiet = _incident(db, "UNVERIFIED_ADMIN_ACTOR", source_id="unverified:x", seen=NOW - timedelta(days=31))
    recent = _incident(db, "UNVERIFIED_ADMIN_ACTOR", source_id="unverified:y", seen=NOW - timedelta(days=1))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, quiet) == "ACKNOWLEDGED"
    assert _state(db, recent) == "OPEN"
