"""첫 계약 월을 안분한 달의 약속 편수가 원장 보고서·운영 스냅샷까지 같은 값으로 간다(D3).

가상 기록만 쓴다. 약속 편수는 보고서 빌더와 같은 `month_content_target_sync`로 읽는다.
"""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import arrow
from sqlalchemy import select
from test_geo_autonomy_hardening import db as db
from test_geo_autonomy_hardening import seed
from test_report_plain_language import _html, _page
from test_report_redesign import monthly_view

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem
from app.services.content_month_target import INITIAL_SCHEDULE_ACTOR, month_content_target_sync
from app.services.monthly_content_operations import build_monthly_content_operations_snapshot

SEPTEMBER = arrow.get(date(2026, 9, 1))


def _prorated_first_month(db):
    """9/14에 첫 일정이 시작해 첫 달 약속이 5편으로 안분된 병원."""

    hospital, schedule = seed(db)
    schedule.active_from = date(2026, 9, 14)
    for row in db.scalars(select(ContentItem)):
        row.total_count = 5
    # 안분은 첫 일정 자동 생성 경로만 한다 — 그 감사행이 안분의 증거다.
    db.add(
        AdminAuditLog(
            hospital_id=hospital.id, actor=INITIAL_SCHEDULE_ACTOR, action="set_schedule",
            target_type="content_schedule", target_id=str(schedule.id), detail={},
        )
    )
    db.flush()
    return hospital


def test_first_month_tile_promises_the_prorated_count_without_shortfall(db):
    hospital = _prorated_first_month(db)
    quota = month_content_target_sync(db, hospital.id, SEPTEMBER, "PLAN_12")
    assert quota == 5

    view = monthly_view(published_count=5, plan_quota=quota, contract_published_count=5)

    tile = view["tiles"][0]
    assert tile["label"] == "약속한 글 발행"
    assert tile["value"] == "5편 중 5편"
    assert view["narrative"].fulfillment_note == "다음 달에도 계획한 글을 일정에 맞춰 올리겠습니다."
    first_page = _page(_html(view), 1)
    assert "약속한 글 발행" in first_page and "5편 중 5편" in first_page
    assert "남은" not in first_page and "12편" not in first_page


def test_first_month_operations_snapshot_has_no_shortfall_warning(db):
    hospital = _prorated_first_month(db)
    quota = month_content_target_sync(db, hospital.id, SEPTEMBER, "PLAN_12")
    published = [
        SimpleNamespace(
            status="PUBLISHED",
            sequence_no=row.sequence_no,
            body_updated_at=None,
            scheduled_date=row.scheduled_date,
            published_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
            post_publish_reviewed_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
            post_publish_reviewed_by="system:ai-review",
        )
        for row in db.scalars(select(ContentItem))
    ]

    snapshot = build_monthly_content_operations_snapshot(
        plan="PLAN_12",
        contract_quota=quota,
        scheduled_items=published,
        published_items=published,
        cutoff_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        contract_published_count=len(published),
    )

    assert snapshot.payload["plan_quota"] == 5
    assert snapshot.payload["shortfall_count"] == 0
    assert not any("약정 콘텐츠" in warning for warning in snapshot.delivery_warnings)


def test_a_regular_month_still_promises_the_plan_quota(db):
    hospital, _schedule = seed(db)  # 9/1 시작, 슬롯이 요금제 편수 12를 기록한다
    assert month_content_target_sync(db, hospital.id, SEPTEMBER, "PLAN_12") == 12


def test_a_plan_upgrade_inside_a_human_first_month_is_not_read_as_prorated(db):
    """사람이 만든 첫 달(PLAN_12, 슬롯 12)에 같은 달 PLAN_16으로 올려도 안분이 아니다."""

    hospital, schedule = seed(db)
    schedule.active_from = date(2026, 9, 14)
    db.flush()

    assert month_content_target_sync(db, hospital.id, SEPTEMBER, "PLAN_16") == 16
