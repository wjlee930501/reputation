"""계약 예정 대비 실제 발행 집계 — 실제 SQL로 고정한다.

여기서 확인하는 것은 mock으로는 확인할 수 없는 것들이다. 기간에 걸친 서비스 구간
겹침(중지된 병원이 조용히 사라지지 않는가), `scheduled_date`와 `first_published_at`
두 조건의 합집합(지연 발행이 실제 발행 주에 잡히는가), 그리고 JSONB 시도 조각을
읽어 재시도와 조치 필요를 나누는 판정이다.
"""

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.content_yield import compute_content_yield
from app.workers.generation_retry_policy import GenerationRetryClass

WEEK_START = date(2026, 9, 7)
WEEK_END = date(2026, 9, 14)
KST = timezone(timedelta(hours=9))


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


def _seed_hospital(
    conn,
    *,
    name: str,
    status: str = "ACTIVE",
    interval: tuple[datetime, datetime | None] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status, site_live) "
            "VALUES (:id, :name, :slug, :status, true)"
        ),
        {
            "id": hospital_id,
            "name": name,
            "slug": f"yield-{uuid.uuid4().hex[:8]}",
            "status": status,
        },
    )
    conn.execute(
        text(
            "INSERT INTO content_schedules (id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hid, 'PLAN_12', '[1, 3]', :active_from)"
        ),
        {"id": schedule_id, "hid": hospital_id, "active_from": date(2026, 9, 1)},
    )
    if interval is not None:
        conn.execute(
            text(
                "INSERT INTO hospital_service_intervals "
                "(id, hospital_id, started_at, ended_at, provenance) "
                "VALUES (:id, :hid, :started_at, :ended_at, 'ACTIVATION')"
            ),
            {
                "id": uuid.uuid4(),
                "hid": hospital_id,
                "started_at": interval[0],
                "ended_at": interval[1],
            },
        )
    return hospital_id, schedule_id


def _seed_item(
    conn,
    hospital_id,
    schedule_id,
    *,
    scheduled_date: date,
    status: str = "DRAFT",
    sequence_no: int = 1,
    first_published_at: datetime | None = None,
    reused_from: uuid.UUID | None = None,
    summary: dict | None = None,
) -> uuid.UUID:
    item_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO content_items "
            "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
            " scheduled_date, status, title, body, content_revision, "
            " published_at, first_published_at, image_reused_from_content_id, "
            " essence_check_summary) "
            "VALUES (:id, :hid, :sid, 'DISEASE', :seq, 12, :d, :status, :title, '본문', 1, "
            " :published_at, :first_published_at, :reused_from, "
            " CAST(:summary AS jsonb))"
        ),
        {
            "id": item_id,
            "hid": hospital_id,
            "sid": schedule_id,
            "seq": sequence_no,
            "d": scheduled_date,
            "status": status,
            "title": f"{scheduled_date} {sequence_no}번 글",
            "published_at": first_published_at,
            "first_published_at": first_published_at,
            "reused_from": reused_from,
            "summary": json.dumps(summary) if summary is not None else None,
        },
    )
    return item_id


def _attempt(reason: str, retry_class: str, *, exhausted_days: int = 0) -> dict:
    return {
        "generation_attempt": {
            "reason": reason,
            "retry_class": retry_class,
            "exhausted_days": exhausted_days,
            "attempt_period": "2026-09-09",
        }
    }


def _fact(facts, name):
    return next(fact for fact in facts if fact.hospital_name == name)


def test_due_published_and_block_states_are_counted_per_hospital(pg_conn, pg_session):
    hospital_id, schedule_id = _seed_hospital(pg_conn, name="수율관측의원")
    # 발행 — 이미지 재사용 표시가 있는 한 건 포함.
    source_id = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        scheduled_date=date(2026, 9, 7),
        status="PUBLISHED",
        sequence_no=1,
        first_published_at=datetime(2026, 9, 7, 23, 0, tzinfo=timezone.utc),
    )
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        scheduled_date=date(2026, 9, 8),
        status="PUBLISHED",
        sequence_no=2,
        first_published_at=datetime(2026, 9, 8, 23, 0, tzinfo=timezone.utc),
        reused_from=source_id,
    )
    # 자동 복구가 아직 소유한 슬롯 — 사람의 일이 아니다.
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        scheduled_date=date(2026, 9, 9),
        sequence_no=3,
        summary=_attempt(
            "GENERATION_REJECTED", GenerationRetryClass.SAMPLE_RECOVERABLE.value
        ),
    )
    # 예산 소진 — 조치 필요.
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        scheduled_date=date(2026, 9, 10),
        sequence_no=4,
        summary=_attempt(
            "GENERATION_REJECTED",
            GenerationRetryClass.SAMPLE_RECOVERABLE.value,
            exhausted_days=3,
        ),
    )
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        scheduled_date=date(2026, 9, 11),
        sequence_no=5,
        summary=_attempt(
            "CONTENT_AI_HARD_FINDING", GenerationRetryClass.OPERATOR_REQUIRED.value
        ),
    )
    # 취소 슬롯은 계약 분모가 아니다.
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        scheduled_date=date(2026, 9, 12),
        status="CANCELLED",
        sequence_no=6,
    )
    # 기간 밖 슬롯.
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        scheduled_date=date(2026, 9, 14),
        sequence_no=7,
    )

    facts = compute_content_yield(
        pg_session, period_start=WEEK_START, period_end=WEEK_END
    )
    fact = _fact(facts, "수율관측의원")

    assert fact.due == 5
    assert fact.published == 2
    assert fact.published_with_reused_image == 1
    assert fact.retrying == 1
    assert fact.operator_required == 2
    assert fact.blocked == 3
    # Slack에 나가는 것은 enum이 아니라 운영자 문구다.
    assert all("_" not in cause for cause in fact.blocked_by_cause)
    assert sum(fact.blocked_by_cause.values()) == 3


def test_late_publication_counts_in_the_week_it_was_first_published(pg_conn, pg_session):
    hospital_id, schedule_id = _seed_hospital(pg_conn, name="지연발행의원")
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        scheduled_date=date(2026, 8, 31),
        status="PUBLISHED",
        sequence_no=1,
        first_published_at=datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc),
    )

    facts = compute_content_yield(
        pg_session, period_start=WEEK_START, period_end=WEEK_END
    )
    fact = _fact(facts, "지연발행의원")

    # 예정은 지난주였고 발행은 이번 주다 — 분자와 분모의 기간 정의가 다르다.
    assert (fact.due, fact.published) == (0, 1)


def test_kst_day_boundaries_decide_the_period(pg_conn, pg_session):
    hospital_id, schedule_id = _seed_hospital(pg_conn, name="경계시각의원")
    # KST 월요일 00:30 = UTC 일요일 15:30. UTC로 자르면 지난주로 새어 나간다.
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        scheduled_date=date(2026, 9, 7),
        status="PUBLISHED",
        sequence_no=1,
        first_published_at=datetime(2026, 9, 7, 0, 30, tzinfo=KST),
    )
    # 다음 주 월요일 00:30 KST — 이번 주가 아니다.
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        scheduled_date=date(2026, 9, 13),
        status="PUBLISHED",
        sequence_no=2,
        first_published_at=datetime(2026, 9, 14, 0, 30, tzinfo=KST),
    )

    fact = _fact(
        compute_content_yield(pg_session, period_start=WEEK_START, period_end=WEEK_END),
        "경계시각의원",
    )

    assert (fact.due, fact.published) == (2, 1)


def test_paused_hospital_with_an_overlapping_interval_stays_in_the_report(
    pg_conn, pg_session
):
    served_id, served_schedule = _seed_hospital(
        pg_conn,
        name="중지된계약의원",
        status="PAUSED",
        interval=(
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 10, tzinfo=timezone.utc),
        ),
    )
    _seed_item(
        pg_conn,
        served_id,
        served_schedule,
        scheduled_date=date(2026, 9, 8),
        sequence_no=1,
        summary=_attempt(
            "CONTENT_IMAGE_NOT_READY",
            GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
        ),
    )
    # 기간 전에 끝난 계약은 대상이 아니다.
    ended_id, ended_schedule = _seed_hospital(
        pg_conn,
        name="이전종료의원",
        status="PAUSED",
        interval=(
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 1, tzinfo=timezone.utc),
        ),
    )
    _seed_item(
        pg_conn, ended_id, ended_schedule, scheduled_date=date(2026, 9, 8), sequence_no=1
    )

    facts = compute_content_yield(
        pg_session, period_start=WEEK_START, period_end=WEEK_END
    )
    names = {fact.hospital_name for fact in facts}

    assert "중지된계약의원" in names
    assert "이전종료의원" not in names
    served = _fact(facts, "중지된계약의원")
    assert (served.due, served.published, served.retrying) == (1, 0, 1)
