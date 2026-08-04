"""공개 후 미확인 큐 — 실제 SQL 집계로 검증.

08:00 자동 발행은 사람 승인 없이 공개되므로, 운영의 위험은 "발행 전 승인"이 아니라
**공개된 뒤 아무도 안 본 시간**이다. 이 집계가 틀리면 그 시간이 화면에서 사라진다.

집계는 GROUP BY + 조건부 COUNT + MIN이라 모의 세션으로는 검증할 수 없다.
"""
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.api.admin.operations import (
    POST_PUBLISH_REVIEW_OVERDUE_HOURS,
    get_attention_queue,
)
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.models.report import MonthlyReport

pytestmark = pytest.mark.asyncio


async def _hospital(db, name: str) -> Hospital:
    hospital = Hospital(name=name, slug=f"clinic-{uuid.uuid4().hex[:12]}")
    db.add(hospital)
    await db.flush()
    # content_items.schedule_id는 NOT NULL — 콘텐츠는 언제나 스케줄에 속한다.
    schedule = ContentSchedule(
        hospital_id=hospital.id,
        plan="PLAN_8",
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
        sequence_no=hospital._test_seq,
        total_count=8,
        scheduled_date=date.today(),
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
    await _content(db, hospital, published_hours_ago=1)
    await _content(db, hospital, published_hours_ago=POST_PUBLISH_REVIEW_OVERDUE_HOURS + 6)
    await _content(db, hospital, published_hours_ago=POST_PUBLISH_REVIEW_OVERDUE_HOURS + 48)

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
    await _content(db, recent, published_hours_ago=2)
    await _content(db, recent, published_hours_ago=3)
    await _content(db, recent, published_hours_ago=4)
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
    await _content(db, second, published_hours_ago=1)
    await _content(db, second, published_hours_ago=POST_PUBLISH_REVIEW_OVERDUE_HOURS + 1)

    result = await get_attention_queue(db)

    assert result.unreviewed_total == sum(h.unreviewed_count for h in result.hospitals)
    assert result.overdue_total == sum(h.overdue_count for h in result.hospitals)
    assert _row(result, first).overdue_count == 1
    assert _row(result, second).unreviewed_count == 2
    assert _row(result, second).overdue_count == 1


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
