"""PR-B의 두 핵심 주장을 실제 Postgres로 본다 — 손으로 만든 더블이 아니라 실제 인덱스·upsert다.

1. 발행기가 거는 자동 이미지 재생성 시스템 실행은 글·KST 날짜 키(`auto-image-regen:<글>:<날짜>`)가
   하루 한 건이다. 같은 날 두 번째 실행은 유일 인덱스(`uq_operation_runs_idempotency_scope`,
   NULL 요청자도 같은 값으로 본다)가 막고, 누적 3건이 되면 한도 조회가 더 사지 않는다.
2. 검수 장애 자동 재검수 한도에 닿은 글의 인시던트는, 앞선 실패들이 남긴 RETRYING 인시던트 위에서
   열려도 OPEN(사람의 일)이 되고 알림은 그 episode에 한 번만 나간다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.workers import generation_incident_control, tasks
from app.workers.generation_incident_control import generation_incident_dedupe_key
from app.workers.generation_retry_policy import GenerationRetryClass

CODE = "CONTENT_AI_REVIEW_UNAVAILABLE"
DAY = date(2026, 10, 5)


@pytest.fixture
def db(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _hospital() -> Hospital:
    return Hospital(
        name="자동 해소 가상의원",
        slug=f"auto-resolve-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
    )


def _image_blocked_post(hospital_id) -> SimpleNamespace:
    """한도 판정·예약이 읽는 열만 가진 글(실행 행은 글을 외래 키로 묶지 않는다)."""

    return SimpleNamespace(id=uuid.uuid4(), hospital_id=hospital_id, essence_check_summary={})


def _system_runs(db, item) -> int:
    return db.scalar(
        select(func.count())
        .select_from(OperationRun)
        .where(OperationRun.idempotency_key.startswith(f"auto-image-regen:{item.id}:"))
    )


def test_the_real_unique_index_allows_one_system_image_run_per_post_and_day(db):
    hospital = _hospital()
    db.add(hospital)
    db.flush()
    item = _image_blocked_post(hospital.id)

    first = tasks._reserve_auto_image_regeneration(db, item, DAY)
    item.essence_check_summary = {}  # 계수가 지워져도(오래된 JSON 덮어쓰기) 키가 막는다
    second = tasks._reserve_auto_image_regeneration(db, item, DAY)

    assert first is not None and first.requested_by_id is None
    assert second is None  # 유일 인덱스가 두 번째 삽입을 거절했다 — savepoint만 되돌렸다
    db.flush()  # 바깥 트랜잭션은 그대로 쓸 수 있다
    assert _system_runs(db, item) == 1


def test_the_total_cap_is_counted_from_the_durable_rows(db):
    hospital = _hospital()
    db.add(hospital)
    db.flush()
    item = _image_blocked_post(hospital.id)
    assessment = SimpleNamespace(code="CONTENT_IMAGE_NOT_READY")
    # 다른 글의 실행은 세지 않는다(같은 병원, 다른 글 id 접두사).
    assert tasks._reserve_auto_image_regeneration(db, _image_blocked_post(hospital.id), DAY)

    decisions = []
    for offset in range(4):
        day = DAY + timedelta(days=offset)
        item.essence_check_summary = {}  # 본문 재작성이 요약을 통째로 다시 썼다
        due = tasks._auto_image_regeneration_due(db, item, assessment, day)
        decisions.append(due)
        if due:
            assert tasks._reserve_auto_image_regeneration(db, item, day) is not None
        # 같은 날 두 번째 판정은 오늘의 행을 보고 사지 않는다.
        assert tasks._auto_image_regeneration_due(db, item, assessment, day) is False

    assert decisions == [True, True, True, False]
    assert _system_runs(db, item) == 3


async def test_a_capped_review_outage_reopens_a_retrying_incident_as_open_and_pages_once(
    pg_async_session, monkeypatch
):
    db = pg_async_session
    hospital = _hospital()
    db.add(hospital)
    await db.flush()
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[1], active_from=DAY
    )
    db.add(schedule)
    await db.flush()
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        scheduled_date=DAY,
        title="가상 글",
        body="진료 전 확인할 점을 안내합니다.",
        status=ContentStatus.DRAFT,
        essence_check_summary={
            "generation_attempt": {
                "reason": CODE,
                "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
                "attempt_period": DAY.isoformat(),
                "review_unavailable_total": 6,
                "review_unavailable_candidate": "c" * 64,
                "review_unavailable_cap_reached": True,
                "next_retry_at": None,
            }
        },
    )
    db.add(item)
    await db.flush()
    run = OperationRun(
        hospital_id=hospital.id, operation_type="REGENERATE_CONTENT", state="FAILED",
        request_payload={},
    )
    db.add(run)
    await db.flush()
    now = datetime.now(UTC)
    # 한도 전의 실패들이 남긴 자동 복구 중 인시던트(기한 안).
    retrying = Incident(
        hospital_id=hospital.id,
        dedupe_key=generation_incident_dedupe_key(item.id, CODE),
        incident_type="CONTENT_GENERATION_FAILED",
        state="RETRYING",
        severity="MEDIUM",
        customer_impact="테스트",
        source_type="CONTENT_GENERATION",
        source_id=str(item.id),
        safe_error_code=CODE,
        next_action="시스템 재시도 중입니다. 다음 예약 배치가 독립 검수를 다시 실행합니다.",
        admin_path="/operations",
        sla_due_at=now + timedelta(hours=6),
        first_seen_at=now - timedelta(hours=10),
        last_seen_at=now - timedelta(hours=4),
        operation_run_id=run.id,
    )
    db.add(retrying)
    await db.flush()

    class _Borrowed:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(generation_incident_control, "get_async_sessionmaker", lambda: _Borrowed)

    async def open_capped():
        return await generation_incident_control.open_generation_incident(
            item_id=item.id,
            hospital_id=hospital.id,
            hospital_name=hospital.name,
            run_id=run.id,
            code=CODE,
            message="독립 검수 공급자 복구 후 자동 재검수를 다시 시도합니다.",
            notify=True,
        )

    incident_id = await open_capped()
    await open_capped()  # 다음 스윕의 같은 관측 — 같은 episode라 다시 알리지 않는다

    assert incident_id == retrying.id
    incident = await db.get(Incident, incident_id, populate_existing=True)
    assert incident.state == "OPEN"
    assert incident.sla_due_at is None
    assert incident.episode_seq == 1
    assert "한도" in incident.next_action
    pages = (
        await db.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.incident_id == incident_id,
                NotificationOutbox.notification_type == "INCIDENT_OPEN",
            )
        )
    ).scalars().all()
    assert len(pages) == 1
