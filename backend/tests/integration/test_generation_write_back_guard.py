"""야간 생성 write-back의 상태 가드 — 실제 SQL로 경쟁 상태를 재현한다.

배경: 야간 배치는 아이템을 claim하면서 커밋하고(그 시점에 행 잠금이 풀린다), 이후
Claude 생성이 끝날 때까지(최대 soft_time_limit) 잠금 없이 진행한다. 그 사이 AE가
Admin에서 해당 아이템을 취소(CANCELLED)할 수 있다.

가드가 없으면 생성 결과 write-back이 status를 DRAFT로 되돌려 취소가 무효화되고,
다음 날 08:00 자동 발행(`status == DRAFT AND body IS NOT NULL`)이 **운영자가 취소한
콘텐츠를 환자에게 공개**한다.

mock 기반 유닛 테스트로는 이 보장을 확인할 수 없다 — 확인해야 하는 것이 rowcount와
WHERE 절의 실제 SQL 동작이기 때문이다.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.content import ContentStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, NotificationOutbox, OperationRun, OperationRunState
from app.workers import generation_incident_control, tasks
from app.workers.generation_batch_run import GenerationBatchRecorder
from app.workers.generation_run_control import GenerationItemState
from app.workers.nightly_generation_batch import write_back_generated_content


@pytest.fixture
def pg_session(pg_conn):
    """테스트 트랜잭션에 묶인 ORM 세션 — 프로덕션 함수를 그대로 호출하기 위한 것."""
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


def _write_back(session, item_id, *, title: str, body: str) -> int:
    """프로덕션 경로와 동일한 함수를 쓴다 — 테스트가 자기 SQL을 검증하지 않도록."""
    return write_back_generated_content(
        session,
        item_id=item_id,
        values={"title": title, "body": body, "status": ContentStatus.DRAFT},
    )


def _seed_item(conn, *, status: str) -> uuid.UUID:
    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    item_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status, site_live) "
            "VALUES (:id, '통합테스트병원', :slug, 'ACTIVE', true)"
        ),
        {"id": hospital_id, "slug": f"itest-{uuid.uuid4().hex[:8]}"},
    )
    conn.execute(
        text(
            "INSERT INTO content_schedules (id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hid, 'PLAN_12', '[1, 3]', :active_from)"
        ),
        {"id": schedule_id, "hid": hospital_id, "active_from": date(2026, 7, 1)},
    )
    conn.execute(
        text(
            "INSERT INTO content_items "
            "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
            " scheduled_date, status) "
            "VALUES (:id, :hid, :sid, 'FAQ', 1, 8, :d, :status)"
        ),
        {
            "id": item_id,
            "hid": hospital_id,
            "sid": schedule_id,
            "d": date(2026, 7, 15),
            "status": status,
        },
    )
    return item_id


def test_write_back_applies_while_the_item_is_still_a_draft(pg_conn, pg_session):
    item_id = _seed_item(pg_conn, status="DRAFT")

    written = _write_back(pg_session, item_id, title="대장내시경 전 준비", body="본문")

    assert written == 1
    row = pg_conn.execute(
        text("SELECT status, title, body FROM content_items WHERE id = :id"), {"id": item_id}
    ).one()
    assert row.status == "DRAFT"
    assert row.title == "대장내시경 전 준비"
    assert row.body == "본문"


def test_write_back_cannot_resurrect_content_cancelled_during_generation(pg_conn, pg_session):
    """AE가 생성 도중 취소했다면 생성 결과는 버려져야 한다."""
    item_id = _seed_item(pg_conn, status="DRAFT")

    # 생성이 도는 동안 AE가 Admin에서 종료 처리.
    pg_conn.execute(
        text("UPDATE content_items SET status = 'CANCELLED' WHERE id = :id"), {"id": item_id}
    )

    written = _write_back(
        pg_session, item_id, title="되살아나면 안 되는 제목", body="되살아나면 안 되는 본문"
    )

    assert written == 0, "취소된 콘텐츠에 생성 결과가 쓰였다"
    row = pg_conn.execute(
        text("SELECT status, title, body FROM content_items WHERE id = :id"), {"id": item_id}
    ).one()
    assert row.status == "CANCELLED"
    assert row.title is None
    # body가 남으면 08:00 자동 발행 쿼리(status=DRAFT AND body IS NOT NULL)의
    # 먹잇감이 될 수 있다. 취소된 항목은 본문 없이 유지되어야 한다.
    assert row.body is None


def test_write_back_does_not_overwrite_an_already_published_item(pg_conn, pg_session):
    """수동 발행이 먼저 끝났다면 야간 결과가 공개본을 덮어쓰지 않는다."""
    item_id = _seed_item(pg_conn, status="PUBLISHED")

    written = _write_back(pg_session, item_id, title="덮어쓰면 안 됨", body="덮어쓰면 안 됨")

    assert written == 0
    row = pg_conn.execute(
        text("SELECT status, title FROM content_items WHERE id = :id"), {"id": item_id}
    ).one()
    assert row.status == "PUBLISHED"
    assert row.title is None


def test_a_dirty_tracked_object_bypasses_the_guard_via_autoflush(pg_conn, pg_session):
    """가드 앞에서 추적 객체를 변경하면 autoflush가 **가드 없는 UPDATE**를 먼저 쏜다.

    이 실패 모드가 실제로 일어났었다: 브리프 플래너가 item을 더럽힌 채로 두면
    `db.execute()`가 autoflush를 먼저 돌려 status 술어 없는 UPDATE를 emit했다.
    헬퍼만 격리 검증하면 이 경로가 보이지 않으므로, 여기서 명시적으로 고정한다.
    """
    from app.models.content import ContentItem

    item_id = _seed_item(pg_conn, status="DRAFT")
    tracked = pg_session.get(ContentItem, item_id)
    assert tracked is not None

    # 운영자가 종료했다(다른 경로에서 커밋된 상태를 흉내).
    pg_conn.execute(
        text("UPDATE content_items SET status = 'CANCELLED' WHERE id = :id"), {"id": item_id}
    )
    pg_session.expire(tracked, ["status"])

    # 가드 앞에서 추적 객체를 더럽힌다 — 플래너가 하던 것과 같은 종류의 변경.
    tracked.meta_description = "플래너가 남긴 변경"
    assert tracked in pg_session.dirty

    written = _write_back(pg_session, item_id, title="되살아나면 안 됨", body="되살아나면 안 됨")

    assert written == 0
    row = pg_conn.execute(
        text("SELECT status, title, body FROM content_items WHERE id = :id"), {"id": item_id}
    ).one()
    assert row.status == "CANCELLED"
    assert row.title is None
    assert row.body is None


def test_unfinished_claims_are_released_so_a_redelivery_can_pick_them_up(pg_conn, pg_session):
    """생성 못 한 슬롯의 claim은 배치 종료 시 해제되어야 한다.

    claim은 행 잠금 해제를 위해 커밋으로 내구화되는데 해제 경로가 없었다. 워커가
    중간에 죽으면 남은 슬롯이 TTL(2시간)까지 잠기고, 재배달된 실행은 claim 필터에
    걸려 아무것도 못 잡은 채 "생성할 것 없음"으로 성공 종료한다.
    """
    from datetime import datetime, timezone

    from app.workers.nightly_generation_batch import release_unfinished_claims

    generated_id = _seed_item(pg_conn, status="DRAFT")
    unfinished_id = _seed_item(pg_conn, status="DRAFT")
    now = datetime.now(timezone.utc)
    pg_conn.execute(
        text("UPDATE content_items SET generation_claimed_at = :t WHERE id IN (:a, :b)"),
        {"t": now, "a": generated_id, "b": unfinished_id},
    )
    # 하나는 본문만 생성됐고 대표 이미지는 아직 없다. 다음 복구 pass가 이미지를
    # 다시 만들 수 있도록 이 슬롯도 claim을 해제해야 한다.
    pg_conn.execute(
        text("UPDATE content_items SET body = '생성된 본문' WHERE id = :id"),
        {"id": generated_id},
    )

    released = release_unfinished_claims(pg_session, [generated_id, unfinished_id])

    assert released == 2, "본문 또는 대표 이미지가 없는 슬롯은 모두 해제되어야 한다"
    rows = dict(
        pg_conn.execute(
            text("SELECT id, generation_claimed_at FROM content_items WHERE id IN (:a, :b)"),
            {"a": generated_id, "b": unfinished_id},
        ).all()
    )
    assert rows[unfinished_id] is None, "미생성 슬롯의 claim이 남아 다음 실행을 막는다"
    assert rows[generated_id] is None, "이미지 없는 슬롯의 claim이 남아 복구를 막는다"


def test_claim_release_is_scoped_to_the_exact_attempt(pg_conn, pg_session):
    """늦은 워커의 finally가 더 최신 claim을 해제하면 안 된다."""
    from datetime import datetime, timedelta, timezone

    from app.workers.nightly_generation_batch import release_unfinished_claims

    item_id = _seed_item(pg_conn, status="DRAFT")
    old_claim = datetime.now(timezone.utc) - timedelta(hours=3)
    new_claim = datetime.now(timezone.utc)
    pg_conn.execute(
        text("UPDATE content_items SET generation_claimed_at = :t WHERE id = :id"),
        {"t": new_claim, "id": item_id},
    )

    released = release_unfinished_claims(
        pg_session,
        [item_id],
        expected_claimed_at=old_claim,
    )

    assert released == 0
    stored = pg_conn.execute(
        text("SELECT generation_claimed_at FROM content_items WHERE id = :id"),
        {"id": item_id},
    ).scalar_one()
    assert stored == new_claim


def test_mixed_generation_batch_finishes_partial_with_retryable_failed_item(pg_conn, pg_session):
    success_id = _seed_item(pg_conn, status="DRAFT")
    failure_id = _seed_item(pg_conn, status="DRAFT")
    hospital_ids = dict(
        pg_conn.execute(
            text("SELECT id, hospital_id FROM content_items WHERE id IN (:a, :b)"),
            {"a": success_id, "b": failure_id},
        ).all()
    )
    recorder = GenerationBatchRecorder(
        pg_session,
        "task-14-partial-batch",
        date(2026, 7, 14),
        date(2026, 7, 15),
    )

    recorder.record(success_id, GenerationItemState.SUCCEEDED)
    recorder.item_run(
        success_id,
        hospital_ids[success_id],
        "REGENERATE_CONTENT",
        OperationRunState.SUCCEEDED,
    )
    recorder.record(
        failure_id,
        GenerationItemState.FAILED,
        safe_error_code="PROVIDER_TIMEOUT",
        safe_error_message="운영 센터에서 재시도해 주세요.",
    )
    failed = recorder.item_run(
        failure_id,
        hospital_ids[failure_id],
        "REGENERATE_CONTENT",
        OperationRunState.FAILED,
        safe_error_code="PROVIDER_TIMEOUT",
        safe_error_message="운영 센터에서 재시도해 주세요.",
    )
    state = recorder.finish()

    row = pg_conn.execute(
        text(
            "SELECT state, total_count, success_count, failure_count, result_summary "
            "FROM operation_runs WHERE id = :id"
        ),
        {"id": recorder.run.id},
    ).one()
    assert state == OperationRunState.PARTIAL
    assert (row.state, row.total_count, row.success_count, row.failure_count) == (
        "PARTIAL",
        2,
        1,
        1,
    )
    failed_payload = row.result_summary["items"][str(failure_id)]
    assert failed_payload["safe_error_code"] == "PROVIDER_TIMEOUT"
    assert failed_payload["next_retry_at"]
    child = pg_conn.execute(
        text(
            "SELECT state, operation_type, parent_run_id, safe_error_code "
            "FROM operation_runs WHERE id = :id"
        ),
        {"id": failed.id},
    ).one()
    assert child.state == "FAILED"
    assert child.operation_type == "REGENERATE_CONTENT"
    assert child.parent_run_id == recorder.run.id
    assert child.safe_error_code == "PROVIDER_TIMEOUT"


def test_all_due_items_behind_live_leases_finish_failed(pg_conn, pg_session, monkeypatch):
    from datetime import datetime, timezone

    item_id = _seed_item(pg_conn, status="DRAFT")
    pg_conn.execute(
        text("UPDATE content_items SET generation_claimed_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc), "id": item_id},
    )
    recorder = GenerationBatchRecorder(
        pg_session,
        f"task-14-all-locked-{uuid.uuid4()}",
        date(2026, 7, 15),
        date(2026, 7, 15),
    )

    def close_async(coroutine):
        coroutine.close()

    monkeypatch.setattr(tasks, "_run_async", close_async)
    stuck_items = tasks.load_stuck_claims(
        pg_session,
        date(2026, 7, 15),
        date(2026, 7, 15),
    )
    tasks._record_locked_generation_items(recorder, stuck_items)
    state = recorder.finish()

    pg_session.expire_all()
    run = pg_session.get(OperationRun, recorder.run.id)
    assert state == OperationRunState.FAILED
    assert run is not None
    assert (run.total_count, run.success_count, run.failure_count) == (1, 0, 1)
    assert run.result_summary["items"][str(item_id)]["safe_error_code"] == (
        "GENERATION_LEASE_ACTIVE"
    )


def test_processed_and_live_lease_items_finish_partial(pg_conn, pg_session, monkeypatch):
    from datetime import datetime, timezone

    success_id = _seed_item(pg_conn, status="DRAFT")
    locked_id = _seed_item(pg_conn, status="DRAFT")
    pg_conn.execute(
        text("UPDATE content_items SET body = 'saved' WHERE id = :id"),
        {"id": success_id},
    )
    pg_conn.execute(
        text("UPDATE content_items SET generation_claimed_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc), "id": locked_id},
    )
    recorder = GenerationBatchRecorder(
        pg_session,
        f"task-14-mixed-locked-{uuid.uuid4()}",
        date(2026, 7, 15),
        date(2026, 7, 15),
    )
    recorder.record(success_id, GenerationItemState.SUCCEEDED)

    def close_async(coroutine):
        coroutine.close()

    monkeypatch.setattr(tasks, "_run_async", close_async)
    stuck_items = tasks.load_stuck_claims(
        pg_session,
        date(2026, 7, 15),
        date(2026, 7, 15),
    )
    tasks._record_locked_generation_items(recorder, stuck_items)
    state = recorder.finish()

    pg_session.expire_all()
    run = pg_session.get(OperationRun, recorder.run.id)
    assert state == OperationRunState.PARTIAL
    assert run is not None
    assert (run.total_count, run.success_count, run.failure_count) == (2, 1, 1)
    assert set(run.result_summary["items"]) == {str(success_id), str(locked_id)}


async def test_generation_failure_stays_silent_before_due_slot_and_retry_recovers_it(
    pg_async_session, monkeypatch
):
    hospital = Hospital(
        id=uuid.uuid4(),
        name="복구통합의원",
        slug=f"recovery-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
    )
    item_id = uuid.uuid4()
    failed_run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="REGENERATE_CONTENT",
        state=OperationRunState.FAILED,
        request_payload={},
        attempt_count=1,
        total_count=1,
        success_count=0,
        failure_count=1,
        skipped_count=0,
        version=1,
    )
    hospital_id = hospital.id
    hospital_name = hospital.name
    failed_run_id = failed_run.id
    pg_async_session.add_all((hospital, failed_run))
    await pg_async_session.commit()

    class SharedSessions:
        def __call__(self):
            return AsyncSession(
                bind=pg_async_session.bind,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )

    monkeypatch.setattr(
        generation_incident_control,
        "get_async_sessionmaker",
        lambda: SharedSessions(),
    )

    incident_id = await generation_incident_control.open_generation_incident(
        item_id=item_id,
        hospital_id=hospital_id,
        hospital_name=hospital_name,
        run_id=failed_run_id,
        code="PROVIDER_TIMEOUT",
        message="운영 센터에서 재시도해 주세요.",
    )

    pg_async_session.expire_all()
    incident = await pg_async_session.get(Incident, incident_id)
    assert incident is not None
    assert incident.state == "OPEN"
    assert incident.safe_error_code == "PROVIDER_TIMEOUT"
    open_outbox = await pg_async_session.scalar(
        select(func.count(NotificationOutbox.id)).where(
            NotificationOutbox.incident_id == incident_id,
            NotificationOutbox.notification_type == "INCIDENT_OPEN",
        )
    )
    # There is no proven due ContentItem/body hole, so provider failure cannot page.
    assert open_outbox == 0

    succeeded_run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="REGENERATE_CONTENT",
        state=OperationRunState.SUCCEEDED,
        parent_run_id=failed_run_id,
        request_payload={},
        attempt_count=1,
        total_count=1,
        success_count=1,
        failure_count=0,
        skipped_count=0,
        version=1,
    )
    succeeded_run_id = succeeded_run.id
    pg_async_session.add(succeeded_run)
    await pg_async_session.commit()

    recovered = await generation_incident_control.recover_generation_incidents(
        item_id,
        hospital_id,
        hospital_name,
        succeeded_run_id,
    )

    assert recovered == 1
    pg_async_session.expire_all()
    incident = await pg_async_session.get(Incident, incident_id)
    assert incident is not None
    assert incident.state == "RECOVERED"
    assert incident.operation_run_id == succeeded_run_id
    recovery_outbox = await pg_async_session.scalar(
        select(func.count(NotificationOutbox.id)).where(
            NotificationOutbox.incident_id == incident_id,
            NotificationOutbox.notification_type == "INCIDENT_RECOVERED",
        )
    )
    # 자동 복구 성공은 운영센터 상태만 닫고 Slack 소음을 만들지 않는다.
    assert recovery_outbox == 0
