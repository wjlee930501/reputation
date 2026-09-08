"""공개 후 미확인 큐 — 실제 SQL 집계로 검증.

08:00 자동 발행은 사람 승인 없이 공개되므로, 운영의 위험은 "발행 전 승인"이 아니라
**공개된 뒤 아무도 안 본 시간**이다. 이 집계가 틀리면 그 시간이 화면에서 사라진다.

집계는 GROUP BY + 조건부 COUNT + MIN이라 모의 세션으로는 검증할 수 없다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from sqlalchemy import event, null, select

from app.api.admin import operations_center
from app.api.admin import operations_center_report_queries as report_queries
from app.api.admin import operations_center_today_queries as today_queries
from app.api.admin.operations import (
    POST_PUBLISH_REVIEW_OVERDUE_HOURS,
    get_attention_queue,
)
from app.api.admin.operations_center_query_common import OperationsFilters, SlaFilter
from app.api.admin.operations_center_read_routes import get_global_incident_detail
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.admin_user import ROLE_OPERATOR, ROLE_OWNER, AdminUser
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.handoff import HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import (
    HospitalServiceInterval,
    MonthlyDeliveryEvent,
    ReportDeliveryEventType,
)
from app.models.operations import (
    Incident,
    IncidentSeverity,
    NotificationOutbox,
    OperationRun,
)
from app.models.report import MonthlyReport
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, compute_sources_snapshot_hash
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)
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
    # 공개 가시성 판정은 승인된 운영 기준을 요구한다 — 기준이 없으면 모든 발행 글이
    # 공개 보류로 잡혀 이 파일의 확인 대기 집계가 통째로 0이 된다.
    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title=f"{name} 홈페이지",
        raw_text="근거 자료 본문",
        content_hash=f"hash-{uuid.uuid4().hex[:12]}",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(UTC),
    )
    db.add(source)
    await db.flush()
    philosophy = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        positioning_statement=f"{name}은 근거 중심으로 충분히 설명합니다.",
        patient_promise="확인된 정보만 환자에게 안내합니다.",
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        approved_at=datetime.now(UTC),
    )
    db.add(philosophy)
    await db.flush()
    hospital._test_schedule_id = schedule.id  # 테스트 편의 — 모델에 없는 임시 속성
    hospital._test_philosophy_id = philosophy.id
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
    withheld: bool = False,
) -> ContentItem:
    published_at = (
        datetime.now(UTC) - timedelta(hours=published_hours_ago)
        if published_hours_ago is not None
        else None
    )
    # uq_content_items_schedule_slot(schedule_id, scheduled_date, sequence_no)
    hospital._test_seq += 1
    # 공개 사이트가 실제로 내보내는 글이어야 "공개 후 확인 필요"에 들어간다 — 인증을
    # 전부 채워 두고, 보류 표본만 이미지 인증을 비운다.
    title = f"{hospital.name} 안내 {hospital._test_seq}"
    image_url = f"https://storage.googleapis.com/reputation-images/content/{'b' * 64}-ok.png"
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
        title=title,
        body="환자 상태에 따라 치료 방향을 설명합니다.",
        faq_question="회복 기간은 얼마나 걸리나요?",
        faq_answer_summary="상태에 따라 다르며 진료 후 안내합니다.",
        references_list=[
            {
                "title": "질병관리청 국가건강정보포털",
                "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo.do",
            }
        ],
        essence_status=ESSENCE_STATUS_ALIGNED,
        content_philosophy_id=hospital._test_philosophy_id,
        image_url=None if withheld else image_url,
        image_policy_verified_at=None if withheld else datetime.now(UTC),
        image_content_hash=None if withheld else image_content_hash_from_url(image_url),
        image_subject_hash=None if withheld else image_subject_hash(ContentType.FAQ, title),
        image_policy_version=None if withheld else IMAGE_POLICY_VERSION,
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


async def test_withheld_published_content_is_counted_apart_from_review_work(pg_async_session):
    """공개 페이지가 숨기는 중인 글은 "공개 후 확인 필요"가 아니다(H-01).

    확인을 누르면 backend가 409로 거절하므로, 확인 대기로 세면 그 행은 영원히 큐에
    남아 24시간 뒤 빨갛게 물든다. 할 일이 다르니 숫자도 나눈다.
    """
    db = pg_async_session
    hospital = await _hospital(db, "공개보류 의원")
    await _content(db, hospital, published_hours_ago=1, sequence_no=1)
    await _content(
        db,
        hospital,
        published_hours_ago=POST_PUBLISH_REVIEW_OVERDUE_HOURS + 6,
        sequence_no=1,
        scheduled_days_ago=1,
        withheld=True,
    )

    result = await get_attention_queue(db)

    row = _row(result, hospital)
    assert row.unreviewed_count == 1
    assert row.overdue_count == 0
    assert row.withheld_count == 1
    assert result.withheld_total >= 1


async def test_hospital_with_only_withheld_content_stays_in_the_queue_last(pg_async_session):
    """확인할 것이 없고 보류만 남은 병원도 목록에서 사라지지 않는다 — 다만 맨 뒤다."""
    db = pg_async_session
    withheld_only = await _hospital(db, "보류만 의원")
    waiting = await _hospital(db, "확인대기 정렬 의원")
    await _content(db, withheld_only, published_hours_ago=300, withheld=True)
    await _content(db, waiting, published_hours_ago=2)

    result = await get_attention_queue(db)

    row = _row(result, withheld_only)
    assert row is not None
    assert row.unreviewed_count == 0
    assert row.oldest_published_at is None
    assert row.withheld_count == 1
    ordered = [h.hospital_id for h in result.hospitals]
    assert ordered.index(waiting.id) < ordered.index(withheld_only.id)


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


# 표본 조회 1 + 승인 기준 묶음 2 + 지난달 원장 보고 1. 공개 가시성은 행마다 판정하지만
# 병원별 승인 기준을 병원마다 조회하면 이 화면 하나가 병원 수에 비례하는 쿼리를 낸다.
_ATTENTION_STATEMENT_BUDGET = 4


async def _attention_query_count(db) -> int:
    statements: list[str] = []

    def count_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db.bind.engine
    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        await get_attention_queue(db)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    return len(statements)


async def test_attention_queue_query_count_is_constant_across_hospitals(pg_async_session):
    db = pg_async_session
    first = await _hospital(db, "쿼리예산 첫 의원")
    await _content(db, first, published_hours_ago=1, sequence_no=1)
    one_count = await _attention_query_count(db)

    for index in range(11):
        extra = await _hospital(db, f"쿼리예산 {index} 의원")
        await _content(db, extra, published_hours_ago=1, sequence_no=1)
        await _content(
            db, extra, published_hours_ago=2, sequence_no=1, scheduled_days_ago=1, withheld=True
        )
    many_count = await _attention_query_count(db)

    result = await get_attention_queue(db)
    assert result.unreviewed_total == 12
    assert result.withheld_total == 11
    assert one_count == _ATTENTION_STATEMENT_BUDGET
    assert many_count == one_count


# ── 지난달 원장 보고 누락·미전달 ──────────────────────────────────────
# 월말 배치 실패는 Slack 한 줄로 지나가고, 그 병원은 다음 달 마지막 날까지 리포트가
# 빈 채로 남는다. 만들어졌어도 원장에게 안 갔으면 운영 실패는 같다.


def _previous_month(now: datetime) -> tuple[int, int]:
    local = now.astimezone(ZoneInfo("Asia/Seoul"))
    return (local.year, local.month - 1) if local.month > 1 else (local.year - 1, 12)


async def _active_hospital(db, name: str, *, created_months_ago: int = 6) -> Hospital:
    hospital = await _hospital(db, name)
    hospital.status = HospitalStatus.ACTIVE
    now = datetime.now(UTC)
    started_at = now - timedelta(days=31 * created_months_ago)
    hospital.created_at = started_at
    db.add(
        HospitalServiceInterval(
            hospital_id=hospital.id,
            started_at=started_at,
            ended_at=None,
            provenance="ACTIVATION",
        )
    )
    await db.flush()
    return hospital


async def _monthly_report(
    db,
    hospital: Hospital,
    *,
    sent: bool,
    version: int = 1,
    supersedes_report_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> MonthlyReport:
    year, month = _previous_month(datetime.now(UTC))
    report = MonthlyReport(
        hospital_id=hospital.id,
        period_year=year,
        period_month=month,
        report_type="MONTHLY",
        version=version,
        supersedes_report_id=supersedes_report_id,
        pdf_path="gs://bucket/report.pdf",
        sent_at=datetime.now(UTC) if sent else None,
        created_at=created_at,
    )
    db.add(report)
    await db.flush()
    return report


async def _delivery_event(
    db,
    report: MonthlyReport,
    event_type: ReportDeliveryEventType,
    *,
    created_at: datetime,
) -> MonthlyDeliveryEvent:
    event = MonthlyDeliveryEvent(
        report_id=report.id,
        event_type=event_type.value,
        created_at=created_at,
    )
    db.add(event)
    await db.flush()
    return event


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


async def test_append_only_delivery_event_overrides_missing_legacy_sent_at(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "이벤트전달 의원")
    report = await _monthly_report(db, hospital, sent=False)
    now = datetime.now(UTC)
    await _delivery_event(db, report, ReportDeliveryEventType.DELIVERED, created_at=now)

    result = await get_attention_queue(db)

    assert hospital.name not in _names(result.reports.missing)
    assert hospital.name not in _names(result.reports.undelivered)


async def test_rescinded_delivery_returns_report_to_undelivered_queue(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "철회 의원")
    report = await _monthly_report(db, hospital, sent=True)
    now = datetime.now(UTC)
    await _delivery_event(db, report, ReportDeliveryEventType.DELIVERED, created_at=now)
    await _delivery_event(
        db,
        report,
        ReportDeliveryEventType.RESCINDED,
        created_at=now + timedelta(minutes=1),
    )

    result = await get_attention_queue(db)

    assert hospital.name not in _names(result.reports.missing)
    entry = next(e for e in result.reports.undelivered if e.hospital_name == hospital.name)
    assert entry.report_id == report.id


async def test_corrected_delivery_remains_effective_for_attention_queue(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "정정 의원")
    report = await _monthly_report(db, hospital, sent=False)
    now = datetime.now(UTC)
    await _delivery_event(db, report, ReportDeliveryEventType.DELIVERED, created_at=now)
    await _delivery_event(
        db,
        report,
        ReportDeliveryEventType.CORRECTED,
        created_at=now + timedelta(minutes=1),
    )

    result = await get_attention_queue(db)

    assert hospital.name not in _names(result.reports.missing)
    assert hospital.name not in _names(result.reports.undelivered)


async def test_latest_monthly_report_version_controls_legacy_attention(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "최신버전 의원")
    created = datetime.now(UTC)
    first = await _monthly_report(db, hospital, sent=True, version=1, created_at=created)
    latest = await _monthly_report(
        db,
        hospital,
        sent=False,
        version=2,
        supersedes_report_id=first.id,
        created_at=created + timedelta(minutes=1),
    )

    result = await get_attention_queue(db)

    assert hospital.name not in _names(result.reports.missing)
    entry = next(e for e in result.reports.undelivered if e.hospital_name == hospital.name)
    assert entry.report_id == latest.id


async def test_operations_reports_queue_uses_delivery_events_not_sent_at(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "운영철회 의원")
    report = await _monthly_report(db, hospital, sent=True)
    now = datetime.now(UTC)
    await _delivery_event(db, report, ReportDeliveryEventType.DELIVERED, created_at=now)
    await _delivery_event(
        db,
        report,
        ReportDeliveryEventType.RESCINDED,
        created_at=now + timedelta(minutes=1),
    )

    _total, rows = await report_queries.load_reports_queue(
        db,
        OperationsFilters(),
        page=1,
        page_size=100,
        overview=False,
        now=now,
    )

    row = next(item for item in rows if item.customer.hospital_id == hospital.id)
    assert row.status == "COVERAGE_INCOMPLETE"
    assert row.report_id == report.id


async def test_operations_reports_queue_reads_only_latest_report_version(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "운영최신버전 의원")
    now = datetime.now(UTC)
    first = await _monthly_report(db, hospital, sent=True, version=1, created_at=now)
    latest = await _monthly_report(
        db,
        hospital,
        sent=False,
        version=2,
        supersedes_report_id=first.id,
        created_at=now + timedelta(minutes=1),
    )

    _total, rows = await report_queries.load_reports_queue(
        db,
        OperationsFilters(),
        page=1,
        page_size=100,
        overview=False,
        now=now,
    )

    row = next(item for item in rows if item.customer.hospital_id == hospital.id)
    assert row.status == "COVERAGE_INCOMPLETE"
    assert row.report_id == latest.id


async def test_report_hospital_scope_puts_the_deep_link_target_on_the_first_page(
    pg_async_session,
):
    db = pg_async_session
    target = await _active_hospital(db, "보고서 링크 대상 의원")
    await _active_hospital(db, "다른 보고서 의원")
    now = datetime.now(UTC)
    year, month = _previous_month(now)

    total, rows = await report_queries.load_reports_queue(
        db,
        OperationsFilters(hospital_id=target.id),
        page=1,
        page_size=1,
        overview=False,
        now=now,
    )

    assert total == 1
    assert len(rows) == 1
    assert rows[0].customer.hospital_id == target.id
    assert rows[0].id == f"report:{target.id}:{year}-{month:02d}"


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
    interval = await db.scalar(
        select(HospitalServiceInterval).where(HospitalServiceInterval.hospital_id == hospital.id)
    )
    assert interval is not None
    interval.ended_at = datetime.now(UTC) - timedelta(days=60)
    await db.flush()

    result = await get_attention_queue(db)

    assert hospital.name not in _names(result.reports.missing)


async def test_prior_month_service_interval_controls_legacy_report_attention(pg_async_session):
    db = pg_async_session
    served_then_paused = await _active_hospital(db, "지난달해지 의원")
    served_then_paused.status = HospitalStatus.PAUSED
    year, month, period_start, _period_end, _closes_at = report_queries._previous_period(
        datetime.now(UTC)
    )
    assert (year, month) == _previous_month(datetime.now(UTC))
    interval = await db.scalar(
        select(HospitalServiceInterval).where(
            HospitalServiceInterval.hospital_id == served_then_paused.id
        )
    )
    assert interval is not None
    interval.ended_at = period_start + timedelta(days=10)

    no_prior_service = await _hospital(db, "구간없는 의원")
    no_prior_service.status = HospitalStatus.ACTIVE
    no_prior_service.created_at = period_start - timedelta(days=90)
    await db.flush()

    result = await get_attention_queue(db)

    assert served_then_paused.name in _names(result.reports.missing)
    assert no_prior_service.name not in _names(result.reports.missing)


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


async def _incident(
    db,
    hospital: Hospital,
    *,
    owner: AdminUser | None = None,
    state: str = "OPEN",
    sla_due_at: datetime | None = None,
    safe_error_code: str = "PROVIDER_TIMEOUT",
) -> Incident:
    incident = Incident(
        hospital_id=hospital.id,
        dedupe_key=f"qa:{uuid.uuid4()}",
        incident_type="PROVIDER_TIMEOUT",
        state=state,
        sla_due_at=sla_due_at,
        severity=IncidentSeverity.HIGH,
        customer_impact="오늘 콘텐츠 초안 생성이 멈췄습니다.",
        owner_id=owner.id if owner else None,
        source_type="content_generation",
        safe_error_code=safe_error_code,
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

    result = await operations_center.get_operations_overview(db=db, actor=actor)

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


async def test_live_hospital_stays_in_onboarding_until_content_schedule_is_ready(
    pg_async_session,
):
    """STEP 5 공개 뒤 STEP 6이 남은 병원이 운영 대기열에서 사라지면 안 된다."""
    db = pg_async_session
    actor = await _operations_actor(db)
    hospital = await _hospital(
        db,
        "공개 후 콘텐츠 준비 의원",
        status=HospitalStatus.ACTIVE,
        site_live=True,
    )
    hospital.schedule_set = False
    await db.flush()

    pending = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.ONBOARDING,
        page=1,
        page_size=100,
        db=db,
        actor=actor,
    )
    assert any(row.customer.hospital_id == hospital.id for row in pending.items)

    hospital.schedule_set = True
    await db.flush()
    completed = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.ONBOARDING,
        page=1,
        page_size=100,
        db=db,
        actor=actor,
    )
    assert all(row.customer.hospital_id != hospital.id for row in completed.items)


async def test_operations_incident_filters_paginate_and_empty(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db, "필터 담당자")
    hospital = await _active_hospital(db, "필터 의원")
    await _incident(db, hospital, owner=actor)
    distinct_cause = await _incident(db, hospital, owner=actor)
    distinct_cause.incident_type = "CONTENT_WRITE_FAILED"
    distinct_cause.safe_error_code = "CONTENT_WRITE_FAILED"
    distinct_cause.safe_error_message = "콘텐츠 저장 작업을 다시 확인해 주세요."
    await db.flush()

    first = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.INCIDENTS,
        owner=actor.name,
        status="OPEN",
        severity="HIGH",
        sla="NONE",
        page=1,
        page_size=1,
        db=db,
        actor=actor,
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
        actor=actor,
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
        actor=actor,
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
        actor=actor,
    )
    onboarding_none = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.ONBOARDING,
        sla="NONE",
        page=1,
        page_size=100,
        db=db,
        actor=actor,
    )
    today_high = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.TODAY,
        severity="HIGH",
        page=1,
        page_size=100,
        db=db,
        actor=actor,
    )
    today_overdue = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.TODAY,
        sla="OVERDUE",
        page=1,
        page_size=100,
        db=db,
        actor=actor,
    )
    reports_medium = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.REPORTS,
        severity="MEDIUM",
        page=1,
        page_size=100,
        db=db,
        actor=actor,
    )
    reports_due = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.REPORTS,
        sla="DUE",
        page=1,
        page_size=100,
        db=db,
        actor=actor,
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


async def test_report_queue_suppresses_monthly_report_while_autonomous_run_is_active(
    pg_async_session,
):
    db = pg_async_session
    now = datetime(2026, 8, 2, tzinfo=UTC)
    hospital = await _active_hospital(db, "자동 월간 생성 중 의원")
    hospital.created_at = datetime(2026, 6, 1, tzinfo=UTC)
    db.add(
        OperationRun(
            hospital_id=hospital.id,
            operation_type="SCHEDULED_MONTHLY_REPORT",
            state="RUNNING",
            request_payload={"source_type": "MONTHLY_SCHEDULE", "source_id": "2026-07"},
            result_summary={"stage": "RUNNING", "period_year": 2026, "period_month": 7},
            started_at=now - timedelta(minutes=20),
        )
    )
    await db.flush()

    total, rows = await report_queries.load_reports_queue(
        db,
        OperationsFilters(),
        page=1,
        page_size=100,
        overview=False,
        now=now,
    )

    assert total == 0
    assert all(row.customer.hospital_id != hospital.id for row in rows)


async def test_report_queue_returns_monthly_report_after_autonomous_run_fails(
    pg_async_session,
):
    db = pg_async_session
    hospital = await _active_hospital(db, "월간 실패 조치 의원")
    hospital.created_at = datetime(2026, 6, 1, tzinfo=UTC)
    db.add(
        OperationRun(
            hospital_id=hospital.id,
            operation_type="SCHEDULED_MONTHLY_REPORT",
            state="FAILED",
            request_payload={"source_type": "MONTHLY_SCHEDULE", "source_id": "2026-07"},
            result_summary={"stage": "FAILED", "period_year": 2026, "period_month": 7},
            completed_at=datetime(2026, 8, 1, 0, 40, tzinfo=UTC),
        )
    )
    await db.flush()

    total, rows = await report_queries.load_reports_queue(
        db,
        OperationsFilters(),
        page=1,
        page_size=100,
        overview=False,
        now=datetime(2026, 8, 2, tzinfo=UTC),
    )

    assert total == 1
    assert [row.customer.hospital_id for row in rows] == [hospital.id]
    assert rows[0].status == "MISSING"


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
        await operations_center.get_operations_overview(db=db, actor=actor)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    return len(statements)


# 온보딩 2(페이지 + 연결 작업) + 오늘 4(보류 후보 1 + 승인 기준 묶음 2 + 페이지 1) +
# 리포트 1 + 인시던트 2(원인 그룹 → 해당 페이지 상세, `load_incidents_queue`의 2-pass).
# 병원 수·행 수가 아니라 대기열 수에만 비례해야 한다.
_OVERVIEW_STATEMENT_BUDGET = 9


async def test_operations_overview_query_count_is_constant_for_one_or_many_rows(pg_async_session):
    db = pg_async_session
    actor = await _operations_actor(db)
    hospital = await _active_hospital(db, "첫 예외 의원")
    await _incident(db, hospital, owner=actor)
    # 오늘의 운영 큐에 후행 검수 표본이 있어야 공개 가시성 판정 경로까지 예산이 지켜진다.
    await _content(db, hospital, published_hours_ago=1, sequence_no=1)
    one_count = await _overview_query_count(db, actor)

    for index in range(24):
        extra = await _active_hospital(db, f"추가 예외 {index} 의원")
        await _incident(db, extra, owner=actor)
        await _content(db, extra, published_hours_ago=1, sequence_no=1)
    many_count = await _overview_query_count(db, actor)

    assert one_count <= _OVERVIEW_STATEMENT_BUDGET
    assert many_count == one_count


async def test_incident_queue_groups_same_cause_in_a_constant_number_of_queries(
    pg_async_session,
):
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
            actor=actor,
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)

    assert result.total == 1
    assert len(result.items) == 1
    assert result.items[0].same_type_count == 25
    assert result.items[0].affected_hospital_count == 1
    # 묶인 인시던트 id를 그대로 실어 보낸다 — 깊은 링크가 대표 행을 찾는 근거다(B4).
    assert len(result.items[0].member_incident_ids) == 25
    # 인시던트 25건이 원인 1건으로 묶여도 쿼리는 2회 고정이다(그룹 판별 pass + 해당
    # 페이지 상세 pass). 건수에 비례해 늘어나면 N+1이므로 이 수치는 상한이자 하한이다.
    # 자동 복구가 맡은 묶음이 함께 있을 때만 맥락 pass 하나가 더 붙는다(아래 테스트).
    assert len(statements) == 2


async def test_automatic_retries_do_not_push_operator_work_off_the_first_page(
    pg_async_session,
):
    """거르기가 묶기·쪽 나누기보다 먼저다.

    약속한 재시도 창이 남은 RETRYING 스물다섯 건이 먼저 묶이면 1페이지를 그 원인들이
    채우고, 사람이 손대야 하는 OPEN 한 건은 다음 페이지로 밀려 보이지 않는다. 총계도
    기계가 맡은 일을 사람의 할 일처럼 센다.
    """
    db = pg_async_session
    actor = await _operations_actor(db)
    hospital = await _active_hospital(db, "자동 재시도 의원")
    within_window = datetime.now(UTC) + timedelta(hours=6)
    for index in range(25):
        await _incident(
            db,
            hospital,
            owner=actor,
            state="RETRYING",
            sla_due_at=within_window,
            # 원인이 서로 달라야 25개 묶음이 되어 실제로 페이지를 채운다.
            safe_error_code=f"AUTOMATIC_RETRY_{index}",
        )
    operator_work = await _incident(
        db, hospital, owner=actor, safe_error_code="OPERATOR_ONLY"
    )

    result = await operations_center.get_operations_queue(
        operations_center.OperationsQueue.INCIDENTS,
        page=1,
        page_size=25,
        db=db,
        actor=actor,
    )

    actionable = [item for item in result.items if item.requires_operator_action]
    assert result.total == 1
    assert [item.incident_id for item in actionable] == [operator_work.id]
    # 자동 복구 중인 건은 버려지지 않는다 — 맥락으로 함께 실려 화면이 접어서 보여준다.
    assert len(result.items) - len(actionable) == 25


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


async def test_assign_route_refuses_an_account_the_screen_never_offers(pg_async_session):
    """배정 라우트는 고를 수 있는 목록과 같은 조건을 요구한다.

    운영 점검 계정은 화면에 뜨지 않는데 요청 본문으로는 지정할 수 있었다. 그렇게 만든
    예외는 담당자는 있는데 아무도 보지 않는 상태가 된다.
    """
    db = pg_async_session
    actor = await _operations_actor(db)
    hidden = await _operations_actor(db, "운영 점검 계정")
    hidden.is_operations_test = True
    hospital = await _active_hospital(db, "배정 검증 의원")
    incident = await _incident(db, hospital)
    await db.flush()

    async def override_get_db():
        yield db

    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    headers = {"X-Admin-Key": "test-admin-key", "X-Admin-Actor": actor.email}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            hidden_owner = await client.post(
                f"/api/v1/admin/operations/hospitals/{hospital.id}/incidents/{incident.id}/assign",
                headers=headers,
                json={
                    "expected_version": incident.version,
                    "reason": "운영 점검 계정으로 배정 시도",
                    "owner_id": str(hidden.id),
                    "sla_due_at": None,
                },
            )
            offered = await client.get(
                f"/api/v1/admin/operations/hospitals/{hospital.id}/incidents/{incident.id}",
                headers=headers,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter

    assert hidden_owner.status_code == 422
    assert hidden_owner.json()["detail"]["code"] == "INVALID_OWNER"
    assert offered.status_code == 200
    assert str(hidden.id) not in [
        account["id"] for account in offered.json()["assignable_accounts"]
    ]


async def _accepted_handoff(db, hospital: Hospital, *, sla_due_at: datetime) -> HospitalHandoff:
    """인수까지 끝난 계약 — ck_hospital_handoffs_state_facts가 요구하는 사실을 모두 채운다."""
    owner = await _operations_actor(db)
    handoff = HospitalHandoff(
        hospital_id=hospital.id,
        state=HandoffState.HANDOFF_ACCEPTED,
        acceptance_source="DIRECT_CREATE",
        sales_owner_id=owner.id,
        ae_owner_id=owner.id,
        contract_reference=f"CTR-{hospital.slug}",
        contract_effective_at=datetime.now(UTC) - timedelta(days=1),
        plan="PLAN_12",
        sla_due_at=sla_due_at,
        accepted_by_id=owner.id,
        accepted_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(handoff)
    await db.flush()
    return handoff


# ── 처리 기한은 그 줄의 일에서 온다 ────────────────────────────────────
# 오늘의 운영과 월간 리포트는 계약 인수 기한(handoff.sla_due_at)을 보여 주면서 상태는
# 다른 기준으로 정했다. 그러면 화면은 그 일과 관계없는 날짜 옆에 "처리 기한 지남"을
# 붙이고, 인수 기한이 없는 병원에서는 날짜 없이 기한만 지났다고 말한다.


async def test_today_queue_deadline_is_the_review_window_not_the_handoff_date(pg_async_session):
    db = pg_async_session
    hospital = await _hospital(db, "검수기한 의원")
    handoff_due = datetime.now(UTC) + timedelta(days=30)
    await _accepted_handoff(db, hospital, sla_due_at=handoff_due)
    content = await _content(db, hospital, published_hours_ago=2, sequence_no=1)
    now = datetime.now(UTC)

    _total, rows = await today_queries.load_today_queue(
        db, OperationsFilters(), page=1, page_size=100, overview=False, now=now
    )

    row = next(item for item in rows if item.content_id == content.id)
    assert row.sla_due_at is not None
    assert row.sla_due_at != handoff_due
    # 발행 후 검수 기한은 공개 시각 + 24시간이다.
    assert abs((row.sla_due_at - (content.published_at + timedelta(hours=24))).total_seconds()) < 1
    assert row.sla_state == "DUE"


async def test_today_queue_folds_the_pre_eight_am_slot_instead_of_dropping_it(pg_async_session):
    """08:00 자동 발행 전의 당일 슬롯은 응답에 남고 플래그만 false여야 한다.

    예전에는 WHERE 절에서 통째로 빼서 목록과 total 양쪽에서 사라졌다. 프런트는 받지도
    못한 행을 접을 수 없고, 스키마가 약속한 requires_operator_action=false 계약과도
    어긋났다.
    """
    db = pg_async_session
    hospital = await _hospital(db, "발행대기 의원")
    due_today = await _content(
        db, hospital, status=ContentStatus.DRAFT, published_hours_ago=None
    )
    overdue = await _content(
        db,
        hospital,
        status=ContentStatus.DRAFT,
        published_hours_ago=None,
        scheduled_days_ago=2,
    )
    seoul = ZoneInfo("Asia/Seoul")
    before = datetime.combine(date.today(), datetime.min.time(), tzinfo=seoul) + timedelta(
        hours=7, minutes=30
    )

    total, rows = await today_queries.load_today_queue(
        db, OperationsFilters(), page=1, page_size=100, overview=False, now=before
    )

    folded = next(item for item in rows if item.content_id == due_today.id)
    still_work = next(item for item in rows if item.content_id == overdue.id)
    assert folded.status == "PUBLISH_DUE"
    assert folded.requires_operator_action is False
    # 접힌 행도 total에 포함된다 — 목록과 개수가 어긋나면 안 된다.
    assert total == len(rows) >= 2
    # 예정일이 이미 지난 슬롯은 시각과 무관하게 사람의 일이다.
    assert still_work.requires_operator_action is True

    after = datetime.combine(date.today(), datetime.min.time(), tzinfo=seoul) + timedelta(hours=9)
    _total, later_rows = await today_queries.load_today_queue(
        db, OperationsFilters(), page=1, page_size=100, overview=False, now=after
    )

    later = next(item for item in later_rows if item.content_id == due_today.id)
    assert later.requires_operator_action is True


async def test_today_queue_uses_the_active_content_run_after_eight_and_keeps_deadline(
    pg_async_session,
):
    db = pg_async_session
    hospital = await _hospital(db, "자동 발행 실행 중 의원")
    content = await _content(
        db, hospital, status=ContentStatus.DRAFT, published_hours_ago=None
    )
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="REGENERATE_CONTENT",
        state="RUNNING",
        request_payload=build_request_payload(
            DispatchPayload("content_item", str(content.id), "content", (str(content.id),))
        ),
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()
    seoul = ZoneInfo("Asia/Seoul")
    after = datetime.combine(date.today(), datetime.min.time(), tzinfo=seoul) + timedelta(hours=9)

    total, rows = await today_queries.load_today_queue(
        db,
        OperationsFilters(hospital_id=hospital.id),
        page=1,
        page_size=100,
        overview=False,
        now=after,
    )

    assert total == 1
    row = rows[0]
    assert row.operation_run_id == run.id
    assert row.requires_operator_action is False
    assert row.sla_due_at is not None
    assert row.action.path.endswith(f"?content={content.id}")


async def test_today_queue_leaves_linked_incident_as_the_single_operator_task(
    pg_async_session,
):
    db = pg_async_session
    hospital = await _hospital(db, "발행 문제 연결 의원")
    content = await _content(
        db, hospital, status=ContentStatus.DRAFT, published_hours_ago=None
    )
    incident = await _incident(db, hospital)
    incident.source_id = str(content.id)
    await db.flush()
    seoul = ZoneInfo("Asia/Seoul")
    after = datetime.combine(date.today(), datetime.min.time(), tzinfo=seoul) + timedelta(hours=9)

    _total, rows = await today_queries.load_today_queue(
        db,
        OperationsFilters(hospital_id=hospital.id),
        page=1,
        page_size=100,
        overview=False,
        now=after,
    )

    row = next(item for item in rows if item.content_id == content.id)
    assert row.incident_id == incident.id
    assert row.requires_operator_action is False


async def test_today_queue_marks_a_review_past_the_window_as_overdue(pg_async_session):
    db = pg_async_session
    hospital = await _hospital(db, "검수초과 의원")
    content = await _content(db, hospital, published_hours_ago=30, sequence_no=1)
    now = datetime.now(UTC)

    _total, rows = await today_queries.load_today_queue(
        db, OperationsFilters(), page=1, page_size=100, overview=False, now=now
    )

    row = next(item for item in rows if item.content_id == content.id)
    assert row.status == "OVERDUE_REVIEW"
    assert row.sla_state == "OVERDUE"
    assert row.sla_due_at < now


async def test_today_queue_sends_a_withheld_item_to_the_reason_not_the_confirmation(
    pg_async_session,
):
    """공개 보류 중인 글에 "콘텐츠 확인" 버튼을 주면 눌러도 409로 거절된다(H-01)."""
    db = pg_async_session
    hospital = await _hospital(db, "오늘의운영 보류 의원")
    withheld = await _content(db, hospital, published_hours_ago=2, sequence_no=1, withheld=True)
    visible = await _content(
        db, hospital, published_hours_ago=3, sequence_no=1, scheduled_days_ago=1
    )
    now = datetime.now(UTC)

    _total, rows = await today_queries.load_today_queue(
        db,
        OperationsFilters(hospital_id=hospital.id),
        page=1,
        page_size=100,
        overview=False,
        now=now,
    )

    blocked = next(item for item in rows if item.content_id == withheld.id)
    assert blocked.status == "WITHHELD_PUBLIC"
    assert blocked.action.kind == "OPEN_CONTENT"
    assert blocked.impact.startswith("공개 보류 — ")
    assert "대표 이미지 재인증 대기" in blocked.impact
    assert blocked.action.path.endswith(f"?content={withheld.id}")
    # 보류도 사람이 손대야 하는 일이다 — 행을 접지 않는다.
    assert blocked.requires_operator_action is True

    normal = next(item for item in rows if item.content_id == visible.id)
    assert normal.status == "REVIEW_PENDING"
    assert normal.action.kind == "REVIEW_CONTENT"


async def test_today_queue_shows_the_recertification_block_as_the_next_action(
    pg_async_session,
):
    """자동 재인증이 사람 결정으로 끝났으면 그 조치를 보류 행에 그대로 보여준다(H-01)."""
    db = pg_async_session
    hospital = await _hospital(db, "재인증 보류 의원")
    withheld = await _content(db, hospital, published_hours_ago=2, sequence_no=1, withheld=True)
    db.add(
        OperationRun(
            hospital_id=hospital.id,
            operation_type="RECERTIFY_PUBLISHED_IMAGE",
            state="FAILED",
            idempotency_key=f"recertify:{withheld.id}:2",
            request_payload=build_request_payload(
                DispatchPayload(
                    "content_item", str(withheld.id), "content", (str(withheld.id),)
                )
            ),
            safe_error_code="PUBLISHED_IMAGE_RECERTIFY_REJECTED",
            safe_error_message="제목이 바뀌어 대표 이미지가 글 주제와 맞지 않습니다.",
            requested_at=datetime.now(UTC) - timedelta(hours=1),
            completed_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    # 나중에 돈 다른 유형의 실행이 최신 실행이 되어도 거절 사유를 가리면 안 된다.
    db.add(
        OperationRun(
            hospital_id=hospital.id,
            operation_type="REGENERATE_CONTENT_IMAGE",
            state="SUCCEEDED",
            idempotency_key=f"regenerate-image:{withheld.id}",
            request_payload=build_request_payload(
                DispatchPayload(
                    "content_item", str(withheld.id), "content", (str(withheld.id),)
                )
            ),
            requested_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
    )
    await db.flush()

    _total, rows = await today_queries.load_today_queue(
        db,
        OperationsFilters(hospital_id=hospital.id),
        page=1,
        page_size=100,
        overview=False,
        now=datetime.now(UTC),
    )

    row = next(item for item in rows if item.content_id == withheld.id)
    assert row.status == "WITHHELD_PUBLIC"
    assert row.next_action == "제목을 되돌리거나, 글을 반려(비공개)해 새 이미지로 재생성하세요."
    assert row.safe_cause == "제목이 바뀌어 대표 이미지가 글 주제와 맞지 않습니다."


async def test_today_queue_status_filter_and_total_agree_on_a_withheld_row(pg_async_session):
    """보류 판정이 SQL 밖에 있으면 status 필터·total·심각도가 서로 다른 답을 낸다(H-01)."""
    db = pg_async_session
    hospital = await _hospital(db, "오늘의운영 필터 의원")
    # 기한을 넘긴 보류 행 — 예전에는 HIGH + OVERDUE로 기록할 수 없는 검수를 재촉했다.
    withheld = await _content(db, hospital, published_hours_ago=30, sequence_no=1, withheld=True)
    visible = await _content(
        db, hospital, published_hours_ago=3, sequence_no=1, scheduled_days_ago=1
    )
    now = datetime.now(UTC)

    async def _load(**filter_kwargs):
        return await today_queries.load_today_queue(
            db,
            OperationsFilters(hospital_id=hospital.id, **filter_kwargs),
            page=1,
            page_size=100,
            overview=False,
            now=now,
        )

    all_total, all_rows = await _load()
    withheld_total, withheld_rows = await _load(status="WITHHELD_PUBLIC")
    review_total, review_rows = await _load(status="REVIEW_PENDING")
    high_total, _high_rows = await _load(severity="HIGH")
    none_total, none_rows = await _load(sla=SlaFilter.NONE)
    overdue_total, overdue_rows = await _load(sla=SlaFilter.OVERDUE)

    assert all_total == len(all_rows) == 2
    assert withheld_total == len(withheld_rows) == 1
    assert withheld_rows[0].content_id == withheld.id
    assert withheld_rows[0].severity == "MEDIUM"
    assert withheld_rows[0].sla_due_at is None
    assert withheld_rows[0].sla_state == "NONE"
    assert review_total == len(review_rows) == 1
    assert review_rows[0].content_id == visible.id
    # 보류 행은 기록할 수 없는 검수라 HIGH·기한 초과 어느 쪽으로도 재촉하지 않는다.
    assert high_total == 0
    assert none_total == len(none_rows) == 1
    assert none_rows[0].content_id == withheld.id
    assert all(row.content_id != withheld.id for row in overdue_rows)
    assert overdue_total == len(overdue_rows)


async def test_reports_queue_has_no_staff_deadline(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "리포트기한 의원")
    await _accepted_handoff(
        db, hospital, sla_due_at=datetime.now(UTC) + timedelta(days=30)
    )
    now = datetime.now(UTC)

    _total, rows = await report_queries.load_reports_queue(
        db, OperationsFilters(), page=1, page_size=100, overview=False, now=now
    )

    row = next(item for item in rows if item.customer.hospital_id == hospital.id)
    assert row.sla_due_at is None
    assert row.sla_state == "NONE"


async def test_reports_queue_ignores_deadline_sla_filters(pg_async_session):
    db = pg_async_session
    hospital = await _active_hospital(db, "리포트마감경계 의원")
    now = datetime.now(UTC)

    total, rows = await report_queries.load_reports_queue(
        db,
        OperationsFilters(),
        page=1,
        page_size=100,
        overview=False,
        now=now,
    )

    row = next(item for item in rows if item.customer.hospital_id == hospital.id)
    assert total >= 1
    assert row.sla_due_at is None
    assert row.sla_state == "NONE"

    none_total, none_rows = await report_queries.load_reports_queue(
        db,
        OperationsFilters(sla=SlaFilter.NONE),
        page=1,
        page_size=100,
        overview=False,
        now=now,
    )

    none_row = next(
        item for item in none_rows if item.customer.hospital_id == hospital.id
    )
    assert none_total >= 1
    assert none_row.sla_due_at is None
    assert none_row.sla_state == "NONE"

    due_total, due_rows = await report_queries.load_reports_queue(
        db,
        OperationsFilters(sla=SlaFilter.DUE),
        page=1,
        page_size=100,
        overview=False,
        now=now,
    )
    overdue_total, overdue_rows = await report_queries.load_reports_queue(
        db,
        OperationsFilters(sla=SlaFilter.OVERDUE),
        page=1,
        page_size=100,
        overview=False,
        now=now,
    )

    assert due_total == 0
    assert due_rows == []
    assert overdue_total == 0
    assert overdue_rows == []


async def test_overview_summary_reports_no_sampled_overdue_count(pg_async_session):
    """5건 표본에서 만든 기한 초과 수는 총계가 아니라서 내보내지 않는다."""
    db = pg_async_session
    actor = await _operations_actor(db)
    hospital = await _active_hospital(db, "표본집계 의원")
    await _incident(db, hospital, owner=actor)

    result = await operations_center.get_operations_overview(db=db, actor=actor)

    for summary in result.queues:
        assert "overdue" not in summary.model_dump()


async def test_incident_detail_offers_only_accounts_the_assign_route_will_accept(
    pg_async_session,
):
    """담당 select의 선택지 = 배정 라우트가 받아 주는 계정. 목록과 서버가 갈리면 422가 된다."""
    db = pg_async_session
    actor = await _operations_actor(db, "담당 OWNER")
    teammate = await _operations_actor(db, "담당 후보", role=ROLE_OPERATOR)
    retired = await _operations_actor(db, "퇴사 계정")
    retired.is_active = False
    qa_account = await _operations_actor(db, "운영 점검 계정")
    qa_account.is_operations_test = True
    await db.flush()
    hospital = await _active_hospital(db, "담당 지정 의원")
    incident = await _incident(db, hospital)

    detail = await operations_center.get_incident_detail(hospital.id, incident.id, db, actor)

    listed = {account.id for account in detail.assignable_accounts}
    assert {actor.id, teammate.id} <= listed
    assert retired.id not in listed
    assert qa_account.id not in listed
    # 행에도 같은 행동이 실린다 — 운영 센터가 담당 지정 버튼을 그릴 근거다.
    assert detail.incident.assign is not None
    assert detail.incident.assign.kind == "ASSIGN_INCIDENT"
    assert detail.incident.assign.enabled is True
    assert detail.incident.assign.requires_version is True
    assert detail.incident.assign.path == (
        f"/api/admin/operations/hospitals/{hospital.id}/incidents/{incident.id}/assign"
    )


async def test_non_owner_sees_the_assign_action_disabled(pg_async_session):
    """담당 지정은 OWNER만 할 수 있다(`require_owner`) — 담당자여도 버튼은 비활성이다."""
    db = pg_async_session
    operator = await _operations_actor(db, "담당 운영자", role=ROLE_OPERATOR)
    hospital = await _active_hospital(db, "권한 표시 의원")
    incident = await _incident(db, hospital, owner=operator)

    detail = await operations_center.get_incident_detail(hospital.id, incident.id, db, operator)

    assert detail.incident.assign is not None
    assert detail.incident.assign.enabled is False
