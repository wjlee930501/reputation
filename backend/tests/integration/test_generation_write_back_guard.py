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
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.content import ContentStatus
from app.workers.nightly_generation_batch import write_back_generated_content


@pytest.fixture
def pg_session(pg_conn):
    """테스트 트랜잭션에 묶인 ORM 세션 — 프로덕션 함수를 그대로 호출하기 위한 것."""
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
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
            "INSERT INTO hospitals (id, name, slug, status) "
            "VALUES (:id, '통합테스트병원', :slug, 'ACTIVE')"
        ),
        {"id": hospital_id, "slug": f"itest-{uuid.uuid4().hex[:8]}"},
    )
    conn.execute(
        text(
            "INSERT INTO content_schedules (id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hid, 'PLAN_8', '[1, 3]', :active_from)"
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
