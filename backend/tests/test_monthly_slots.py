"""다음 달 콘텐츠 슬롯 자동 생성 회귀 테스트 (실 Postgres).

이 로직의 결함은 전부 "어떤 행이 이미 있는가"를 SQL로 판정하는 지점에서 나왔으므로
mock DB로는 재현되지 않는다. tests/integration/conftest.py와 같은 가용성 정책을 쓴다 —
INTEGRATION_DATABASE_URL이 명시되면(CI) 접속 실패는 하드 실패, 로컬이면 skip.
"""
import os
import uuid
from datetime import date

import arrow
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.content import (
    PLAN_DISTRIBUTION,
    ContentItem,
    ContentSchedule,
    ContentStatus,
    ContentType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.workers.monthly_slots import create_next_month_slots_for_schedule

DEFAULT_URL = "postgresql://reputation:reputation@localhost:5434/reputation_test"
_EXPLICIT_URL = os.getenv("INTEGRATION_DATABASE_URL")
INTEGRATION_URL = _EXPLICIT_URL or DEFAULT_URL
INTEGRATION_REQUIRED = bool(_EXPLICIT_URL)

# 대상 월은 2026-08 고정 — 실행 시점에 따라 달라지면 발행 가능 요일 수가 바뀌어 테스트가
# 계절적으로 깨진다.
NEXT_MONTH = arrow.get("2026-08-01")
MONTH_START = date(2026, 8, 1)
MONTH_END = date(2026, 8, 31)


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(INTEGRATION_URL, future=True)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        reason = f"No integration Postgres at {INTEGRATION_URL}: {exc.__class__.__name__}: {exc}"
        if INTEGRATION_REQUIRED:
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
    return engine


@pytest.fixture
def db(pg_engine):
    """테스트마다 롤백되는 실 세션."""
    conn = pg_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _make_schedule(
    db,
    *,
    plan: str = "PLAN_16",
    publish_days: list[int] | None = None,
    active_from: date = date(2026, 1, 1),
    status: HospitalStatus = HospitalStatus.ACTIVE,
) -> ContentSchedule:
    suffix = uuid.uuid4().hex[:10]
    hospital = Hospital(name=f"슬롯테스트의원-{suffix}", slug=f"slot-test-{suffix}", status=status)
    db.add(hospital)
    db.flush()
    schedule = ContentSchedule(
        hospital_id=hospital.id,
        plan=plan,
        publish_days=publish_days if publish_days is not None else [0, 1, 2, 3, 4],
        active_from=active_from,
        is_active=True,
    )
    db.add(schedule)
    db.flush()
    return schedule


def _add_item(
    db,
    schedule: ContentSchedule,
    *,
    scheduled_date: date,
    sequence_no: int,
    carried_over_from: date | None = None,
) -> ContentItem:
    item = ContentItem(
        hospital_id=schedule.hospital_id,
        schedule_id=schedule.id,
        content_type=ContentType.FAQ,
        sequence_no=sequence_no,
        total_count=16,
        scheduled_date=scheduled_date,
        status=ContentStatus.DRAFT,
        carried_over_from=carried_over_from,
    )
    db.add(item)
    db.flush()
    return item


def _planned_sequences(db, schedule: ContentSchedule) -> set[int]:
    return set(
        db.execute(
            select(ContentItem.sequence_no).where(
                ContentItem.schedule_id == schedule.id,
                ContentItem.carried_over_from.is_(None),
                ContentItem.scheduled_date >= MONTH_START,
                ContentItem.scheduled_date <= MONTH_END,
            )
        ).scalars().all()
    )


def _run(db, schedule: ContentSchedule) -> bool:
    return create_next_month_slots_for_schedule(
        db, schedule, NEXT_MONTH, MONTH_START, MONTH_END
    )


def test_carried_over_item_does_not_block_the_whole_month(db):
    """이월 1건이 다음 달 16편 전체 생성을 막지 않는다 (결함 1)."""
    schedule = _make_schedule(db)
    _add_item(
        db,
        schedule,
        scheduled_date=date(2026, 8, 3),
        sequence_no=99,
        carried_over_from=date(2026, 7, 28),
    )

    assert _run(db, schedule) is True
    assert _planned_sequences(db, schedule) == set(range(1, 17))


def test_unrelated_schedule_items_do_not_block_generation(db):
    """같은 병원의 다른 스케줄 행이 판정을 오염시키지 않는다 (결함 1)."""
    schedule = _make_schedule(db)
    other = ContentSchedule(
        hospital_id=schedule.hospital_id,
        plan="PLAN_8",
        publish_days=[0, 2],
        active_from=date(2026, 1, 1),
        is_active=False,
    )
    db.add(other)
    db.flush()
    _add_item(db, other, scheduled_date=date(2026, 8, 5), sequence_no=1)

    assert _run(db, schedule) is True
    assert _planned_sequences(db, schedule) == set(range(1, 17))


def test_partial_slots_are_completed_without_duplicates(db):
    """중단된 이전 배치가 남긴 부분 슬롯은 빈 순번만 채운다 (결함 1)."""
    schedule = _make_schedule(db)
    for seq in (1, 2, 3):
        _add_item(db, schedule, scheduled_date=date(2026, 8, seq), sequence_no=seq)

    assert _run(db, schedule) is True
    assert _planned_sequences(db, schedule) == set(range(1, 17))
    # 이미 있던 순번은 다시 만들지 않았다.
    rows = db.execute(
        select(ContentItem.scheduled_date).where(
            ContentItem.schedule_id == schedule.id, ContentItem.sequence_no == 1
        )
    ).scalars().all()
    assert rows == [date(2026, 8, 1)]


def test_complete_month_is_idempotent(db):
    schedule = _make_schedule(db)

    assert _run(db, schedule) is True
    assert _run(db, schedule) is False
    assert len(_planned_sequences(db, schedule)) == sum(PLAN_DISTRIBUTION["PLAN_16"].values())


def test_schedule_activating_after_target_month_is_skipped(db):
    """7월 배치가 active_from=2026-09-01 스케줄의 8월 슬롯을 만들면 한 달 일찍 발행된다 (결함 2)."""
    schedule = _make_schedule(db, active_from=date(2026, 9, 1))

    assert _run(db, schedule) is False
    assert _planned_sequences(db, schedule) == set()


def test_activation_inside_target_month_only_uses_dates_from_active_from(db):
    """활성화가 대상 월 중간이면 그 날짜 이후로만 발행한다 (결함 2)."""
    schedule = _make_schedule(
        db, plan="PLAN_8", publish_days=[0, 1, 2, 3, 4, 5, 6], active_from=date(2026, 8, 10)
    )

    assert _run(db, schedule) is True
    dates = db.execute(
        select(ContentItem.scheduled_date).where(ContentItem.schedule_id == schedule.id)
    ).scalars().all()
    assert len(dates) == sum(PLAN_DISTRIBUTION["PLAN_8"].values())
    assert min(dates) >= date(2026, 8, 10)


def test_active_from_on_the_last_day_of_month_is_not_treated_as_future(db):
    """경계 — active_from이 대상 월 마지막 날이면 아직 '완전히 이전'이 아니다."""
    schedule = _make_schedule(db, plan="PLAN_8", publish_days=[0, 1, 2, 3, 4, 5, 6],
                              active_from=MONTH_END)

    # 8/31 하루에 PLAN_8(8편)은 들어가지 않으므로 캘린더가 명시적으로 거부한다 —
    # 조용히 한 달 앞당겨 발행하는 것보다 실패 알림이 낫다.
    with pytest.raises(ValueError):
        _run(db, schedule)


def test_inactive_hospital_status_is_skipped(db):
    schedule = _make_schedule(db, status=HospitalStatus.PAUSED)

    assert _run(db, schedule) is False
    assert _planned_sequences(db, schedule) == set()
