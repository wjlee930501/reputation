"""문구가 말한 다음 자동 복구 시각에 실제 생성 스윕이 이 슬롯을 claim한다(#187 3차 B', 실제 SQL).

18:00 공급자 실패(다음 시도 22:00) → 22:00:30에 다시 그린 원고 미생성 조치 문구는 다음 날
01:00을 말한다. 실제 태스크 본문(창)·로더(SQL 후보 + claim 전 적격 술어)를 23:00 야간 배치와
01:00 복구 스윕으로 차례로 돌려, 옛 문구가 말하던 23:00에는 집지 않고 문구가 말하는 01:00에만
집는다는 것을 본다. 배포(서명 봉투 발행)만 가로챈다.
"""

import contextlib
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import arrow
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus
from app.workers import (
    generation_incident_control,
    generation_retry_policy,
    nightly_generation_batch,
    tasks,
)

KST = ZoneInfo("Asia/Seoul")
SLOT = date(2026, 9, 16)


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


def _freeze(monkeypatch, moment: datetime) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, nightly_generation_batch, generation_incident_control):
        monkeypatch.setattr(module, "datetime", _Frozen)
    monkeypatch.setattr(tasks.arrow, "now", lambda tz=None: arrow.get(moment).to(tz or "UTC"))


def _seed_empty_slot(conn) -> uuid.UUID:
    hospital_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status, site_live) "
            "VALUES (:id, '복구시각의원', :slug, 'ACTIVE', true)"
        ),
        {"id": hospital_id, "slug": f"announce-{uuid.uuid4().hex[:8]}"},
    )
    schedule_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO content_schedules (id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hospital_id, 'PLAN_12', '[1]'::json, DATE '2026-09-01')"
        ),
        {"id": schedule_id, "hospital_id": hospital_id},
    )
    item_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO content_items (id, hospital_id, schedule_id, content_type, sequence_no, "
            "total_count, scheduled_date, status, content_revision) VALUES "
            "(:id, :hospital_id, :schedule_id, 'FAQ', 1, 12, :scheduled_date, :status, 1)"
        ),
        {
            "id": item_id,
            "hospital_id": hospital_id,
            "schedule_id": schedule_id,
            "scheduled_date": SLOT,
            "status": ContentStatus.DRAFT.value,
        },
    )
    return item_id


def _run_sweep(monkeypatch, session, task, at: datetime) -> set[uuid.UUID]:
    """실제 태스크 본문을 ``at``에 돌리고, 로더가 claim해 배포하려 한 슬롯을 돌려준다."""

    claimed: set[uuid.UUID] = set()

    def dispatch(_db, _recorder, item, **_kwargs):
        claimed.add(item.id)
        return True

    _freeze(monkeypatch, at)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: contextlib.nullcontext(session))
    monkeypatch.setattr(tasks, "swap_exhausted_topics", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks,
        "GenerationBatchRecorder",
        lambda *_args, **_kwargs: SimpleNamespace(
            finish=lambda: None, record=lambda *_a, **_k: None
        ),
    )
    monkeypatch.setattr(tasks, "_dispatch_generation_item", dispatch)
    monkeypatch.setattr(tasks, "_page_morning_stored_publication_gates", lambda *_a, **_k: None)
    task.run()
    return claimed


def test_the_real_sweep_claims_the_slot_only_at_the_announced_hour(
    pg_conn, pg_session, monkeypatch
):
    item_id = _seed_empty_slot(pg_conn)
    row = pg_session.get(ContentItem, item_id)

    _freeze(monkeypatch, datetime(2026, 9, 16, 18, 0, tzinfo=KST))
    tasks._remember_generation_attempt(pg_session, row, None, "PROVIDER_TIMEOUT")
    stored = row.essence_check_summary["generation_attempt"]
    assert datetime.fromisoformat(stored["next_retry_at"]) == datetime(2026, 9, 16, 22, tzinfo=KST)

    render = datetime(2026, 9, 16, 22, 0, 30, tzinfo=KST)
    announced = generation_incident_control.announced_recovery_time(row, render.astimezone(UTC))
    assert announced == datetime(2026, 9, 17, 1, tzinfo=KST)

    # 옛 문구가 말하던 시각(다음 정시 스윕 = 23:00 야간 배치)에는 이 슬롯을 집지 않는다.
    earlier = datetime(2026, 9, 16, 23, tzinfo=KST)
    assert item_id not in _run_sweep(
        monkeypatch, pg_session, tasks.nightly_content_generation, earlier
    )
    pg_session.refresh(row)
    assert row.generation_claim_token is None

    # 문구가 말하는 시각의 복구 스윕이 집는다.
    assert item_id in _run_sweep(
        monkeypatch, pg_session, tasks.overnight_content_generation_recovery, announced
    )
    pg_session.refresh(row)
    assert row.generation_claim_token is not None


def test_a_slot_moved_by_the_backlog_recovery_is_claimed_at_the_newly_announced_hour(
    pg_conn, pg_session, monkeypatch
):
    """X 15:00 창 밖(X-8) 글의 실패(저장 23:30, 백로그 판정) → 22:30 백로그가 X+1로 옮김 → 22:45 렌더.

    저장된 시각은 아직 오지 않았지만 생성 스윕 시각이 아니다. 23:00 야간 배치는 옮긴 날짜를 창에
    담아도 기한 전이라 집지 않고, 문구가 새로 말하는 다음 날 01:00 복구 스윕이 집는다(#187 3차).
    """

    item_id = _seed_empty_slot(pg_conn)
    row = pg_session.get(ContentItem, item_id)
    row.scheduled_date = date(2026, 9, 8)
    pg_session.flush()

    _freeze(monkeypatch, datetime(2026, 9, 16, 15, 0, tzinfo=KST))
    tasks._remember_generation_attempt(pg_session, row, None, "PROVIDER_TIMEOUT")
    stored = row.essence_check_summary["generation_attempt"]
    assert datetime.fromisoformat(stored["next_retry_at"]) == datetime(
        2026, 9, 16, 23, 30, tzinfo=KST
    )
    # `content_backlog_recovery.reconcile`처럼 기록은 두고 날짜만 옮긴다.
    row.scheduled_date = date(2026, 9, 17)
    pg_session.flush()

    render = datetime(2026, 9, 16, 22, 45, tzinfo=KST)
    announced = generation_incident_control.announced_recovery_time(row, render.astimezone(UTC))
    assert announced == datetime(2026, 9, 17, 1, tzinfo=KST)

    assert item_id not in _run_sweep(
        monkeypatch, pg_session, tasks.nightly_content_generation, datetime(2026, 9, 16, 23, tzinfo=KST)
    )
    pg_session.refresh(row)
    assert row.generation_claim_token is None

    assert item_id in _run_sweep(
        monkeypatch, pg_session, tasks.overnight_content_generation_recovery, announced
    )
    pg_session.refresh(row)
    assert row.generation_claim_token is not None
