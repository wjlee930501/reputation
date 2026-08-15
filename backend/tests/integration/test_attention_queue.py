"""공개 후 미확인 큐 — 실제 SQL 집계로 검증.

08:00 자동 발행은 사람 승인 없이 공개되므로, 운영의 위험은 "발행 전 승인"이 아니라
**공개된 뒤 아무도 안 본 시간**이다. 이 집계가 틀리면 그 시간이 화면에서 사라진다.

집계는 GROUP BY + 조건부 COUNT + MIN이라 모의 세션으로는 검증할 수 없다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from sqlalchemy import event, null

from app.api.admin import operations_center
from app.api.admin.operations import (
    POST_PUBLISH_REVIEW_OVERDUE_HOURS,
    get_attention_queue,
)
from app.api.admin.operations_center_read_routes import get_global_incident_detail
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.admin_user import ROLE_OPERATOR, ROLE_OWNER, AdminUser
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import (
    Incident,
    IncidentSeverity,
    NotificationOutbox,
    OperationRun,
)
from app.models.report import MonthlyReport
from app.services.operation_run_payloads import DispatchPayload, build_request_payload

pytestmark = pytest.mark.asyncio


async def _hospital(
    db,
    name: str,
    *,
    status: HospitalStatus = HospitalStatus.ACTIVE,
    site_live: bool = True,
) -> Hospital:
    hospital = Hospital(
        name=name,
        slug=f"clinic-{uuid.uuid4().hex[:12]}",
        status=status,
        site_live=site_live,
    )
    db.add(hospital)
    await db.flush()
    # content_items.schedule_id는 NOT NULL — 콘텐츠는 언제나 스케줄에 속한다.
    schedule = ContentSchedule(
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[1, 4],
        active_from=date.today(),
    )
    db.add(schedule)
    await db.flush()
    hospital._test_schedule_id = schedule.id  # 테스트 편의 — 모델에 없는 임시 속성
    hospital._test_seq = 0
    return hospital


async def _content(
    db,
    hospital: Hospital,
    *,
    status: ContentStatus = ContentStatus.PUBLISHED,
    published_hours_ago: float | None = 1,
    reviewed: bool = False,
    sequence_no: int | None = None,
    scheduled_days_ago: int = 0,
) -> ContentItem:
    published_at = (
        datetime.now(UTC) - timedelta(hours=published_hours_ago)
        if published_hours_ago is not None
        else None
    )
    # uq_content_items_schedule_slot(schedule_id, scheduled_date, sequence_no)
    hospital._test_seq += 1
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=hospital._test_schedule_id,
        content_type=ContentType.FAQ,
        sequence_no=sequence_no or hospital._test_seq,
        total_count=8,
        scheduled_date=date.today() - timedelta(days=scheduled_days_ago),
        status=status,
        published_at=published_at,
        post_publish_reviewed_at=datetime.now(UTC) if reviewed else None,
    )
    db.add(item)
    await db.flush()
    return item


def _row(result, hospital: Hospital):
    return next((h for h in result.hospitals if h.hospital_id == hospital.id), None)


async def test_counts_only_published_and_unreviewed_content(pg_async_session):
    db = pg_async_session
    hospital = await _hospital(db, "확인대기 의원")
    await _content(db, hospital)                                   # 세어야 함
    await _content(db, hospital, reviewed=True)                    # 이미 확인 — 제외
    await _content(db, hospital, status=ContentStatus.DRAFT)       # 미공개 — 제외
    await _content(db, hospital, status=ContentStatus.CANCELLED)   # 종료 — 제외

    result = await get_attention_queue(db)

    row = _row(result, hospital)
    assert row is not None
    assert row.unreviewed_count == 1


async def test_separates_overdue_from_freshly_published(pg_async_session):
    """1시간 미확인과 이틀 미확인이 같은 숫자로 보이면 큐가 아무것도 알려주지 않는다."""
    db = pg_async_session
    hospital = await _hospital(db, "노후 의원")
    await _content(db, hospital, published_hours_ago=1, sequence_no=1)
    await _content(
        db,
        hospital,
        published_hours_ago=POST_PUBLISH_REVIEW_OVERDUE_HOURS + 6,
        sequence_no=1,
        scheduled_days_ago=1,
    )
    await _content(
        db,
        hospital,
        published_hours_ago=POST_PUBLISH_REVIEW_OVERDUE_HOURS + 48,
        sequence_no=1,
        scheduled_days_ago=2,
    )

    result = await get_attention_queue(db)

    row = _row(result, hospital)
    assert row.unreviewed_count == 3
    assert row.overdue_count == 2
    assert result.overdue_hours == POST_PUBLISH_REVIEW_OVERDUE_HOURS


async def test_boundary_content_is_not_counted_as_overdue(pg_async_session):
    """경계 직전(23시간)은 아직 밀린 것이 아니다 — 경보 피로를 만들지 않는다."""
    db = pg_async_session
    hospital = await _hospital(db, "경계 의원")
    await _content(db, hospital, published_hours_ago=POST_PUBLISH_REVIEW_OVERDUE_HOURS - 1)

    row = _row(await get_attention_queue(db), hospital)

    assert row.unreviewed_count == 1
    assert row.overdue_count == 0


async def test_oldest_waiting_hospital_comes_first(pg_async_session):
    """큐 정렬 기준은 건수가 아니라 방치된 시간이다."""
    db = pg_async_session
    recent = await _hospital(db, "최근 의원")
    stale = await _hospital(db, "방치 의원")
    # 건수는 recent가 더 많지만, 오래 방치된 stale이 위로 와야 한다.
    await _content(db, recent, published_hours_ago=2, sequence_no=1)
    await _content(
        db, recent, published_hours_ago=3, sequence_no=1, scheduled_days_ago=1
    )
    await _content(
        db, recent, published_hours_ago=4, sequence_no=1, scheduled_days_ago=2
    )
    await _content(db, stale, published_hours_ago=200)

    result = await get_attention_queue(db)
    ordered = [h.hospital_id for h in result.hospitals if h.hospital_id in {recent.id, stale.id}]

    assert ordered[0] == stale.id


async def test_hospital_with_nothing_pending_is_absent(pg_async_session):
    db = pg_async_session
    clean = await _hospital(db, "깨끗한 의원")
    await _content(db, clean, reviewed=True)

    result = await get_attention_queue(db)

    assert _row(result, clean) is None


async def test_totals_add_up_across_hospitals(pg_async_session):
    db = pg_async_session
    first = await _hospital(db, "가 의원")
    second = await _hospital(db, "나 의원")
    await _content(db, first, published_hours_ago=POST_PUBLISH_REVIEW_OVERDUE_HOURS + 1)
    await _content(db, second, published_hours_ago=1, sequence_no=1)
    await _content(
        db,
        second,
        published_hours_ago=POST_PUBLISH_REVIEW_OVERDUE_HOURS + 1,
        sequence_no=1,
        scheduled_days_ago=1,
    )

    result = await get_attention_queue(db)

    assert result.unreviewed_total == sum(h.unreviewed_count for h in result.hospitals)
    assert result.overdue_total == sum(h.overdue_count for h in result.hospitals)
    assert _row(result, first).overdue_count == 1
    assert _row(result, second).unreviewed_count == 2
    assert _row(result, second).overdue_count == 1


async def test_non_sample_publications_do_not_create_human_work(pg_async_session):
    db = pg_async_session
    hospital = await _hospital(db, "자동관제 의원")
    await _content(db, hospital, sequence_no=2)
    await _content(db, hospital, sequence_no=3)

    result = await get_attention_queue(db)

    assert _row(result, hospital) is None


async def test_paused_or_non_live_hospitals_do_not_create_human_work(pg_async_session):
    db = pg_async_session
    paused = await _hospital(db, "중지 의원", status=HospitalStatus.PAUSED)
    non_live = await _hospital(db, "비공개 의원", site_live=False)
    await _content(db, paused, published_hours_ago=48)
    await _content(db, non_live, published_hours_ago=48)

    result = await get_attention_queue(db)

    assert _row(result, paused) is None
    assert _row(result, non_live) is None


# ── 지난달 원장 보고 누락·미전달 ──────────────────────────────────────
# 월말 배치 실패는 Slack 한 줄로 지나가고, 그 병원은 다음 달 마지막 날까지 리포트가
# 빈 채로 남는다. 만들어졌어도 원장에게 안 갔으면 운영 실패는 같다.


def _previous_month(now: datetime) -> tuple[int, int]:
    return (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)


async def _active_hospital(db, name: str, *, created_months_ago: int = 6) -> Hospital:
    hospital = await _hospital(db, name)
    hospital.status = HospitalStatus.ACTIVE
    now = datetime.now(UTC)
    hospital.created_at = now - timedelta(days=31 * created_months_ago)
    await db.flush()
    return hospital


async def _monthly_report(db, hospital: Hospital, *, sent: bool) -> MonthlyReport:
    year, month = _previous_month(datetime.now(UTC))
    report = MonthlyReport(
        hospital_id=hospital.id,
        period_year=year,
        period_month=month,
        report_type="MONTHLY",
        pdf_path="gs://bucket/report.pdf",
        sent_at=datetime.now(UTC) if sent else None,
    )
    db.add(report)
    await db.flush()
    return report


def _names(entries) -> set[str]:
    return {entry.hospital_name for entry in entries}


async def test_reports_target_the_previous_month_not_the_current_one(pg_async_session):
    """이번 달 리포트는 월말에 생긴다 — 그 전에 '없음'으로 세면 매일 거짓 경보다."""
    result = await get_attention_queue(pg_async_session)

    assert (result.reports.period_year, result.reports.period_month) == _previous_month(
        datetime.now(UTC)
    )


async def test_an_active_hospital_without_last_months_report_is_flagged(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "리포트없는 의원")

    result = await get_attention_queue(db)

    assert hospital.name in _names(result.reports.missing)
    assert hospital.name not in _names(result.reports.undelivered)


async def test_a_generated_but_unsent_report_is_flagged_separately(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "미전달 의원")
    report = await _monthly_report(db, hospital, sent=False)

    result = await get_attention_queue(db)

    assert hospital.name not in _names(result.reports.missing)
    entry = next(e for e in result.reports.undelivered if e.hospital_name == hospital.name)
    assert entry.report_id == report.id


async def test_a_delivered_report_disappears_from_the_queue(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "전달완료 의원")
    await _monthly_report(db, hospital, sent=True)

    result = await get_attention_queue(db)

    assert hospital.name not in _names(result.reports.missing)
    assert hospital.name not in _names(result.reports.undelivered)


async def test_a_hospital_that_did_not_exist_yet_is_not_blamed(pg_async_session):
    """이번 달에 막 온보딩한 병원에 지난달 리포트가 없는 건 정상이다."""
    db = pg_async_session
    hospital = await _hospital(db, "신규 의원")
    hospital.status = HospitalStatus.ACTIVE
    hospital.created_at = datetime.now(UTC)
    await db.flush()

    result = await get_attention_queue(db)

    assert hospital.name not in _names(result.reports.missing)


async def test_hospitals_that_are_not_live_are_not_expected_to_have_reports(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "온보딩중 의원")
    hospital.status = HospitalStatus.ONBOARDING
    await db.flush()

    result = await get_attention_queue(db)

    assert hospital.name not in _names(result.reports.missing)


# ── 통합 운영 센터 읽기 모델 ──────────────────────────────────────────


async def _operations_actor(db, name: str = "AE QA", *, role: str = ROLE_OWNER) -> AdminUser:
    actor = AdminUser(
        email=f"{uuid.uuid4().hex}@example.com",
        name=name,
        role=role,
        password_hash="pbkdf2_sha256$1$c2FsdA$ZGlnZXN0",
        is_active=True,
    )
    db.add(actor)
    await db.flush()
    return actor


async def _incident(db, hospital: Hospital, *, owner: AdminUser | None = None) -> Incident:
    incident = Incident(
        hospital_id=hospital.id,
        dedupe_key=f"qa:{uuid.uuid4()}",
        incident_type="PROVIDER_TIMEOUT",
        state="OPEN",
        severity=IncidentSeverity.HIGH,
        customer_impact="오늘 콘텐츠 초안 생성이 멈췄습니다.",
        owner_id=owner.id if owner else None,
        source_type="content_generation",
        safe_error_code="PROVIDER_TIMEOUT",
        safe_error_message="AI 공급자 응답이 지연되고 있습니다.",
        next_action="작업을 다시 시도해 주세요.",
        admin_path=f"/hospitals/{hospital.id}/content",
    )
    db.add(incident)
    await db.flush()
    return incident


async def test_operations_overview_returns_all_four_operator_queues(pg_async_session):
    """한 화면이 고객·영향·담당자·다음 행동을 공통 형식으로 답한다."""
    db = pg_async_session
    actor = await _operations_actor(db)
    onboarding = await _hospital(
        db,
        "온보딩 의원",
        status=HospitalStatus.ONBOARDING,
        site_live=False,
    )
    today = await _hospital(db, "금일 의원")
    today.status = HospitalStatus.ACTIVE
    today.site_live = True
    await db.flush()
    await _content(db, today, published_hours_ago=2)
    report = await _active_hospital(db, "월간 의원")
    incident_hospital = await _active_hospital(db, "예외 의원")
    await _incident(db, incident_hospital, owner=actor)

    result = await operations_center.get_operations_overview(db=db, _actor=actor)

    counts = {summary.queue: summary.total for summary in result.queues}
    assert counts[operations_center.OperationsQueue.ONBOARDING] >= 1
    assert counts[operations_center.OperationsQueue.TODAY] >= 1
    assert counts[operations_center.OperationsQueue.REPORTS] >= 1
    assert counts[operations_center.OperationsQueue.INCIDENTS] >= 1
    rows = [
        row
        for row in result.items
        if row.customer.hospital_id in {onboarding.id, today.id, report.id, incident_hospital.id}
    ]
    assert rows
    assert all(row.impact and row.next_action and row.action.path for row in rows)
    assert all(row.history is not None for row in rows)


async def test_operations_incident_filters_paginate_and_empty(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db, "필터 담당자")
    hospital = await _active_hospital(db, "필터 의원")
    await _incident(db, hospital, owner=actor)
    await _incident(db, hospital, owner=actor)

    first = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.INCIDENTS,
        owner=actor.name,
        status="OPEN",
        severity="HIGH",
        sla="NONE",
        page=1,
        page_size=1,
        db=db,
        _actor=actor,
    )
    second = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.INCIDENTS,
        owner=actor.name,
        status="OPEN",
        severity="HIGH",
        sla="NONE",
        page=2,
        page_size=1,
        db=db,
        _actor=actor,
    )
    empty = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.INCIDENTS,
        owner="없는 담당자",
        status="OPEN",
        severity="HIGH",
        sla="NONE",
        page=1,
        page_size=25,
        db=db,
        _actor=actor,
    )

    assert first.total >= 2
    assert first.items[0].id != second.items[0].id
    assert empty.total == 0
    assert empty.items == []


async def test_non_incident_queue_filters_apply_to_projected_severity_and_sla(
    pg_async_session,
):
    db = pg_async_session
    actor = await _operations_actor(db)
    onboarding = await _hospital(
        db,
        "필터 온보딩 의원",
        status=HospitalStatus.ONBOARDING,
        site_live=False,
    )
    today = await _hospital(db, "필터 금일 의원")
    content = await _content(db, today, published_hours_ago=1)
    report = await _active_hospital(db, "필터 월간 의원")

    onboarding_high = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.ONBOARDING,
        severity="HIGH",
        page=1,
        page_size=100,
        db=db,
        _actor=actor,
    )
    onboarding_none = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.ONBOARDING,
        sla="NONE",
        page=1,
        page_size=100,
        db=db,
        _actor=actor,
    )
    today_high = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.TODAY,
        severity="HIGH",
        page=1,
        page_size=100,
        db=db,
        _actor=actor,
    )
    today_overdue = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.TODAY,
        sla="OVERDUE",
        page=1,
        page_size=100,
        db=db,
        _actor=actor,
    )
    reports_medium = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.REPORTS,
        severity="MEDIUM",
        page=1,
        page_size=100,
        db=db,
        _actor=actor,
    )
    reports_due = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.REPORTS,
        sla="DUE",
        page=1,
        page_size=100,
        db=db,
        _actor=actor,
    )

    assert all(row.customer.hospital_id != onboarding.id for row in onboarding_high.items)
    assert any(row.customer.hospital_id == onboarding.id for row in onboarding_none.items)
    assert all(row.content_id != content.id for row in today_high.items)
    assert all(row.content_id != content.id for row in today_overdue.items)
    assert reports_medium.total == 0
    assert reports_medium.items == []
    assert reports_due.total == 0
    assert reports_due.items == []
    assert report.id is not None


async def test_operations_cross_hospital_and_illegal_ack_fail_closed(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db)
    first = await _active_hospital(db, "A 의원")
    second = await _active_hospital(db, "B 의원")
    incident = await _incident(db, second, owner=actor)

    with pytest.raises(HTTPException) as wrong_tenant:
        await operations_center.get_incident_detail(first.id, incident.id, db, actor)
    with pytest.raises(HTTPException) as illegal_state:
        await operations_center.acknowledge_operations_incident(
            second.id,
            incident.id,
            operations_center.VersionedReasonRequest(
                expected_version=incident.version,
                reason="복구 여부 확인 후 처리",
            ),
            db,
            actor,
        )

    assert wrong_tenant.value.status_code == 404
    assert wrong_tenant.value.detail["code"] == "INCIDENT_NOT_FOUND"
    assert illegal_state.value.status_code == 409
    assert illegal_state.value.detail["code"] == "INCIDENT_TRANSITION_CONFLICT"
    assert illegal_state.value.detail["current_state"] == "OPEN"
    assert illegal_state.value.detail["refetch_path"].endswith(str(incident.id))


async def test_operations_stale_assignment_returns_current_version(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db)
    hospital = await _active_hospital(db, "동시 수정 의원")
    incident = await _incident(db, hospital, owner=actor)

    with pytest.raises(HTTPException) as stale:
        await operations_center.assign_operations_incident(
            hospital.id,
            incident.id,
            operations_center.IncidentAssignRequest(
                expected_version=incident.version + 10,
                owner_id=actor.id,
                sla_due_at=datetime.now(UTC) + timedelta(hours=2),
                reason="당일 운영 담당 지정",
            ),
            db,
            actor,
        )

    assert stale.value.status_code == 409
    assert stale.value.detail["code"] == "INCIDENT_VERSION_CONFLICT"
    assert stale.value.detail["current_version"] == incident.version
    assert stale.value.detail["current_state"] == "OPEN"


async def test_operator_cannot_reassign_incident_even_when_assigned(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db, role=ROLE_OPERATOR)
    hospital = await _active_hospital(db, "권한 의원")
    incident = await _incident(db, hospital, owner=actor)

    with pytest.raises(HTTPException) as forbidden:
        await operations_center.assign_operations_incident(
            hospital.id,
            incident.id,
            operations_center.IncidentAssignRequest(
                expected_version=incident.version,
                owner_id=actor.id,
                sla_due_at=datetime.now(UTC) + timedelta(hours=2),
                reason="담당자 재지정 요청",
            ),
            db,
            actor,
        )

    assert forbidden.value.status_code == 403
    assert forbidden.value.detail["code"] == "OWNER_REQUIRED"


async def test_recovery_requires_observed_linked_success_before_ack(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db, role=ROLE_OPERATOR)
    hospital = await _active_hospital(db, "복구 의원")
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="TRIGGER_V0_REPORT",
        state="FAILED",
        request_payload=build_request_payload(
            DispatchPayload("hospital", str(hospital.id), "reports", (str(hospital.id),))
        ),
        completed_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()
    incident = await _incident(db, hospital, owner=actor)
    incident.state = "RETRYING"
    incident.operation_run_id = run.id
    await db.flush()
    body = operations_center.VersionedReasonRequest(
        expected_version=incident.version, reason="연결 작업 성공 여부 확인"
    )

    with pytest.raises(HTTPException) as unobserved:
        await operations_center.recover_operations_incident(
            hospital.id, incident.id, body, db, actor
        )
    run.state = "SUCCEEDED"
    await db.flush()
    recovered = await operations_center.recover_operations_incident(
        hospital.id, incident.id, body, db, actor
    )
    acknowledged = await operations_center.acknowledge_operations_incident(
        hospital.id,
        incident.id,
        operations_center.VersionedReasonRequest(
            expected_version=recovered.incident.version,
            reason="복구 사실 확인 완료",
        ),
        db,
        actor,
    )

    assert unobserved.value.status_code == 409
    assert unobserved.value.detail["code"] == "INCIDENT_RECOVERY_NOT_OBSERVED"
    assert recovered.incident.status == "RECOVERED"
    assert acknowledged.incident.status == "ACKNOWLEDGED"


async def test_global_incident_is_owner_only(pg_async_session):
    db = pg_async_session
    owner = await _operations_actor(db)
    operator = await _operations_actor(db, role=ROLE_OPERATOR)
    incident = Incident(
        hospital_id=None,
        dedupe_key=f"global:{uuid.uuid4()}",
        incident_type="CONFIGURATION_ERROR",
        state="OPEN",
        severity="CRITICAL",
        customer_impact="전체 Slack 알림 전송이 중단됐습니다.",
        source_type="notification_dispatch",
        next_action="Slack 연결 설정을 확인해 주세요.",
        admin_path="/operations",
    )
    db.add(incident)
    await db.flush()

    detail = await get_global_incident_detail(incident.id, db, owner)
    with pytest.raises(HTTPException) as forbidden:
        await get_global_incident_detail(incident.id, db, operator)

    assert detail.incident.customer.hospital_id is None
    assert detail.incident.customer.name == "전체 시스템"
    assert forbidden.value.status_code == 403
    assert forbidden.value.detail["code"] == "OWNER_REQUIRED"


class _FakeTaskResult:
    def __init__(self, task_id: str):
        self.id = task_id


class _FakeTask:
    def __init__(self):
        self.calls: list[dict] = []

    def apply_async(self, *, args, queue, headers, task_id):
        self.calls.append({"args": args, "queue": queue, "headers": headers})
        return _FakeTaskResult(task_id)


async def test_assigned_operator_retry_is_idempotent_and_server_allowlisted(
    pg_async_session, monkeypatch
):
    db = pg_async_session
    actor = await _operations_actor(db, role=ROLE_OPERATOR)
    hospital = await _active_hospital(db, "재시도 의원")
    previous = OperationRun(
        hospital_id=hospital.id,
        operation_type="TRIGGER_V0_REPORT",
        state="FAILED",
        task_id=str(uuid.uuid4()),
        request_payload=build_request_payload(
            DispatchPayload(
                target_type="hospital",
                target_id=str(hospital.id),
                queue="reports",
                task_args=(str(hospital.id),),
            )
        ),
        safe_error_code="BROKER_UNAVAILABLE",
        safe_error_message="작업 큐 연결에 실패했습니다.",
        completed_at=datetime.now(UTC),
    )
    db.add(previous)
    await db.flush()
    incident = await _incident(db, hospital, owner=actor)
    incident.operation_run_id = previous.id
    await db.flush()
    task = _FakeTask()
    monkeypatch.setitem(
        operations_center._TASK_POLICIES,
        "TRIGGER_V0_REPORT",
        operations_center._TaskPolicy(task, "reports", "hospital", 1),
    )
    body = operations_center.OperationRetryRequest(reason="공급자 복구 확인 후 재시도")

    first = await operations_center.retry_operations_run(
        hospital.id, previous.id, body, "retry-key", db, actor
    )
    second = await operations_center.retry_operations_run(
        hospital.id, previous.id, body, "retry-key", db, actor
    )

    assert first.run_id == second.run_id
    assert first.parent_run_id == previous.id
    assert first.state == "QUEUED"
    assert len(task.calls) == 1
    assert task.calls[0]["queue"] == "reports"
    assert task.calls[0]["args"] == [str(hospital.id)]


async def test_outbox_retry_is_scoped_and_never_returns_payload(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db)
    hospital = await _active_hospital(db, "알림 의원")
    row = NotificationOutbox(
        hospital_id=hospital.id,
        dedupe_key=f"qa:{uuid.uuid4()}",
        notification_type="INCIDENT_OPEN",
        channel="SLACK",
        state="FAILED",
        payload={"text": "secret-bearing-provider-payload"},
        fallback_text="운영 이슈가 발생했습니다.",
        attempt_count=3,
        max_attempts=3,
        next_attempt_at=null(),
        safe_error_code="INVALID_PAYLOAD",
        safe_error_message="Slack 요청 형식을 확인해 주세요.",
    )
    db.add(row)
    await db.flush()

    response = await operations_center.retry_operations_notification(
        hospital.id,
        row.id,
        operations_center.NotificationRetryRequest(
            expected_version=row.version,
            reason="Slack 설정 수정 후 재시도",
        ),
        db,
        actor,
    )

    assert response.state == "RETRYING"
    assert response.next_attempt_at is not None
    assert "payload" not in response.model_dump()
    assert "provider_response" not in response.model_dump()


async def _overview_query_count(db, actor: AdminUser) -> int:
    statements: list[str] = []

    def count_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db.bind.engine
    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        await operations_center.get_operations_overview(db=db, _actor=actor)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    return len(statements)


async def test_operations_overview_query_count_is_constant_for_one_or_many_rows(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db)
    hospital = await _active_hospital(db, "첫 예외 의원")
    await _incident(db, hospital, owner=actor)
    one_count = await _overview_query_count(db, actor)

    for index in range(24):
        extra = await _active_hospital(db, f"추가 예외 {index} 의원")
        await _incident(db, extra, owner=actor)
    many_count = await _overview_query_count(db, actor)

    assert one_count <= 5
    assert many_count == one_count


async def test_incident_queue_uses_count_plus_page_only(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db)
    hospital = await _active_hospital(db, "쿼리 예산 의원")
    for _index in range(25):
        await _incident(db, hospital, owner=actor)
    statements: list[str] = []

    def count_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db.bind.engine
    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        result = await operations_center.get_operations_queue(
            operations_center.OperationsQueue.INCIDENTS,
            page=1,
            page_size=10,
            db=db,
            _actor=actor,
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)

    assert result.total >= 25
    assert len(result.items) == 10
    assert len(statements) == 2


async def test_operations_http_surface_returns_typed_scoping_and_conflict_errors(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db)
    first = await _active_hospital(db, "HTTP A 의원")
    second = await _active_hospital(db, "HTTP B 의원")
    incident = await _incident(db, second, owner=actor)

    async def override_get_db():
        yield db

    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    headers = {
        "X-Admin-Key": "test-admin-key",
        "X-Admin-Actor": actor.email,
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            overview = await client.get(
                "/api/v1/admin/operations/overview",
                params={"owner": actor.name, "status": "OPEN"},
                headers=headers,
            )
            wrong_tenant = await client.get(
                f"/api/v1/admin/operations/hospitals/{first.id}/incidents/{incident.id}",
                headers=headers,
            )
            conflict = await client.post(
                f"/api/v1/admin/operations/hospitals/{second.id}/incidents/{incident.id}/ack",
                headers=headers,
                json={
                    "expected_version": incident.version + 5,
                    "reason": "최신 상태 확인",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter

    assert overview.status_code == 200
    assert wrong_tenant.status_code == 404
    assert wrong_tenant.json()["detail"]["code"] == "INCIDENT_NOT_FOUND"
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "INCIDENT_VERSION_CONFLICT"
    assert conflict.json()["detail"]["refetch_path"].startswith("/api/admin/operations/")
