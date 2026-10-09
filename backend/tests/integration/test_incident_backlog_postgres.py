"""열린 사고를 종류별 DB 근거로 닫고, 근거가 없으면 열어 둔다(2026-10-02)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.models.report import MonthlyReport
from app.workers.incident_backlog import close_resolved_backlog_incidents

NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _hospital(db) -> Hospital:
    hospital = Hospital(
        name="사고 백로그 가상의원",
        slug=f"backlog-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
    )
    db.add(hospital)
    db.flush()
    return hospital


def _incident(db, incident_type, *, hospital=None, source_id=None, seen=NOW - timedelta(days=30)):
    incident = Incident(
        hospital_id=hospital.id if hospital else None,
        dedupe_key=f"backlog-test:{uuid.uuid4()}",
        incident_type=incident_type,
        state="OPEN",
        severity="HIGH",
        customer_impact="테스트",
        source_type="TEST",
        source_id=source_id,
        next_action="테스트",
        admin_path="/operations",
        first_seen_at=seen,
        last_seen_at=seen,
    )
    db.add(incident)
    db.flush()
    return incident


def _state(db, incident) -> str:
    db.expire_all()
    return db.get(Incident, incident.id).state


def _sov_run(db, hospital, at, period, state="SUCCEEDED"):
    summary_key = "measurement_week" if "W" in period else "measurement_month"
    db.add(
        OperationRun(
            hospital_id=hospital.id, operation_type="RUN_SOV", state=state,
            request_payload={}, requested_at=at,
            idempotency_key=f"backlog-sov:{hospital.id}:{period}",
            result_summary={summary_key: period},
        )
    )
    db.flush()


def test_measurement_failure_closes_only_for_the_same_period(db):
    hospital = _hospital(db)
    weekly = _incident(
        db,
        "WEEKLY_SOV_MEASUREMENT_FAILED",
        hospital=hospital,
        source_id=f"{hospital.id}:2026-W39",
    )
    old_month = _incident(
        db,
        "MONTHLY_SOV_MEASUREMENT_FAILED",
        hospital=hospital,
        source_id=f"{hospital.id}:2026-08",
    )
    _sov_run(db, hospital, NOW - timedelta(days=20), "2026-W39")
    _sov_run(db, hospital, NOW - timedelta(days=10), "2026-09")

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, weekly) == "ACKNOWLEDGED"
    # A different month's success cannot recover the unresolved August outcome.
    assert _state(db, old_month) == "OPEN"


def test_past_weeks_and_budget_periods_close_but_current_ones_stay(db):
    old_week = _incident(db, "SOV_HIGH_PRIORITY_CAP_EXCEEDED", source_id="2026-W36")
    this_week = _incident(db, "SOV_HIGH_PRIORITY_CAP_EXCEEDED", source_id="2026-W40")
    old_day = _incident(db, "COST_GUARD_LIMIT_REACHED", source_id="sov:daily:20260817:hard")
    old_month = _incident(db, "COST_GUARD_LIMIT_REACHED", source_id="sov:monthly:202608:hard")
    this_month = _incident(db, "COST_GUARD_LIMIT_REACHED", source_id="sov:monthly:202610:hard")

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, old_week) == "ACKNOWLEDGED"
    assert _state(db, this_week) == "OPEN"
    assert _state(db, old_day) == "ACKNOWLEDGED"
    assert _state(db, old_month) == "ACKNOWLEDGED"
    assert _state(db, this_month) == "OPEN"


def test_a_v0_failure_closes_once_a_v0_report_exists(db):
    hospital = _hospital(db)
    incident = _incident(db, "V0_REPORT_FAILED", hospital=hospital)
    db.add(
        MonthlyReport(
            hospital_id=hospital.id, period_year=2026, period_month=8, report_type="V0",
            created_at=NOW - timedelta(days=29),
        )
    )
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"


def test_a_generation_failure_closes_once_the_post_is_published(db):
    hospital = _hospital(db)
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[1], active_from=date(2026, 8, 1)
    )
    db.add(schedule)
    db.flush()
    item = ContentItem(
        hospital_id=hospital.id, schedule_id=schedule.id, content_type=ContentType.DISEASE,
        sequence_no=1, total_count=12, scheduled_date=date(2026, 8, 20), title="가상 글",
        status=ContentStatus.PUBLISHED, first_published_at=NOW - timedelta(days=29),
    )
    db.add(item)
    db.flush()
    incident = _incident(db, "CONTENT_GENERATION_FAILED", hospital=hospital, source_id=str(item.id))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"


@pytest.mark.parametrize("status,expected", [(302, "ACKNOWLEDGED"), (None, "OPEN")])
def test_a_redirected_slack_delivery_is_not_a_delivery_check(db, status, expected):
    row = NotificationOutbox(
        dedupe_key=f"backlog-test:{uuid.uuid4()}",
        notification_type="INCIDENT_OPEN",
        channel="SLACK_DEV",
        state="HOLD",
        next_attempt_at=None,
        safe_error_code="DELIVERY_OUTCOME_UNKNOWN",
        payload={},
        fallback_text="테스트",
        provider_response={"http_status": status} if status else None,
    )
    db.add(row)
    db.flush()
    incident = _incident(db, "NOTIFICATION_DELIVERY_UNKNOWN", source_id=str(row.id))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == expected
    db.expire_all()
    # 전달되지 않은 것이 확실한 행은 '보낼 채널 없음' 보류로 넘긴다 — 발송기가 다시 보내거나
    # 지난 알림이면 종결한다. 근거가 없으면 행을 건드리지 않는다.
    expected_code = "SLACK_CHANNEL_UNAVAILABLE" if status else "DELIVERY_OUTCOME_UNKNOWN"
    assert db.get(NotificationOutbox, row.id).safe_error_code == expected_code


def test_retrying_incidents_belong_to_automation(db):
    incident = _incident(db, "COST_GUARD_LIMIT_REACHED", source_id="sov:daily:20260817:hard")
    incident.state = "RETRYING"
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "RETRYING"


def _unknown_row(
    db, *, state="HOLD", channel="SLACK_DEV", channel_used=None
) -> NotificationOutbox:
    row = NotificationOutbox(
        dedupe_key=f"backlog-test:{uuid.uuid4()}",
        notification_type="INCIDENT_OPEN",
        channel=channel,
        state=state,
        next_attempt_at=None,
        safe_error_code="DELIVERY_OUTCOME_UNKNOWN",
        payload={},
        fallback_text="테스트",
        sent_at=NOW - timedelta(days=1) if state == "SENT" else None,
        provider_response={"channel_used": channel_used} if channel_used else None,
    )
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize("state", ["SENT", "FAILED"])
def test_an_unknown_delivery_closes_once_the_row_reaches_a_final_state(db, state):
    # 수동 재시도로 그 알림이 결국 전달됐거나(SENT) 실패로 끝났다(FAILED는 별도 사고가 맡는다).
    row = _unknown_row(db, state=state)
    incident = _incident(db, "NOTIFICATION_DELIVERY_UNKNOWN", source_id=str(row.id))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"


def test_an_unknown_delivery_on_a_channel_that_was_dead_then_recovered_closes(db):
    # 그 채널이 죽어 있던 동안 관측된 '수신 불명'은 전달되지 않은 것이다. 채널이 복구됐으면 닫는다.
    row = _unknown_row(db)
    incident = _incident(
        db, "NOTIFICATION_DELIVERY_UNKNOWN", source_id=str(row.id), seen=NOW - timedelta(days=10)
    )
    channel = _incident(
        db, "NOTIFICATION_DELIVERY_FAILED", source_id="SLACK_DEV", seen=NOW - timedelta(days=15)
    )
    channel.source_type = "NOTIFICATION_OUTBOX"
    channel.state = "ACKNOWLEDGED"
    channel.recovered_at = NOW - timedelta(days=1)
    channel.acknowledged_at = NOW - timedelta(days=1)
    db.flush()
    unrelated = _incident(
        db, "NOTIFICATION_DELIVERY_UNKNOWN", source_id=str(_unknown_row(db, channel="SLACK").id)
    )

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"
    assert _state(db, unrelated) == "OPEN"
    assert db.get(NotificationOutbox, row.id).safe_error_code == "SLACK_CHANNEL_UNAVAILABLE"


def test_the_dead_channel_window_matches_the_channel_actually_used(db):
    # 개발 행이 운영 채널로 대체 전송됐다면, 판정 기준은 개발 채널이 아니라 운영 채널이다.
    rerouted = _unknown_row(db, channel="SLACK_DEV", channel_used="SLACK")
    rerouted_incident = _incident(
        db, "NOTIFICATION_DELIVERY_UNKNOWN", source_id=str(rerouted.id), seen=NOW - timedelta(days=10)
    )
    channel = _incident(
        db, "NOTIFICATION_DELIVERY_FAILED", source_id="SLACK_DEV", seen=NOW - timedelta(days=15)
    )
    channel.source_type = "NOTIFICATION_OUTBOX"
    channel.state = "ACKNOWLEDGED"
    channel.recovered_at = NOW - timedelta(days=1)
    channel.acknowledged_at = NOW - timedelta(days=1)
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    # 운영 채널은 죽어 있지 않았다 — 실제로 받았을 수 있으므로 열어 둔다.
    assert _state(db, rerouted_incident) == "OPEN"
    assert db.get(NotificationOutbox, rerouted.id).safe_error_code == "DELIVERY_OUTCOME_UNKNOWN"


def test_unresolvable_unknown_deliveries_do_not_starve_resolvable_ones(db):
    # 근거 없는 오래된 수신 불명 사고가 배치 앞자리를 다 차지해도 닫을 수 있는 사고는 닫힌다.
    for _ in range(9):
        _incident(
            db,
            "NOTIFICATION_DELIVERY_UNKNOWN",
            source_id=str(_unknown_row(db, channel="SLACK").id),
            seen=NOW - timedelta(days=60),
        )
    closable = _incident(
        db, "NOTIFICATION_DELIVERY_UNKNOWN", source_id=str(_unknown_row(db, state="SENT").id)
    )

    close_resolved_backlog_incidents(db, limit=2, now=NOW)

    assert _state(db, closable) == "ACKNOWLEDGED"
