"""생성 리드 타임 D-3 — 로더 순서·같은 날 재시도를 실제 Postgres로 고정한다(W3).

1. 로더는 예정일 오름차순이 먼저다. 창이 사흘 앞까지 넓어져도 상한(50)이 사흘 뒤 슬롯(이월
   포함)에 먼저 쓰이지 않고, 지난·내일 슬롯이 claim된다.
2. 자정 뒤(01:00) 교체된 슬롯은 오늘 하루 예산이 그대로라 같은 스윕이 새 주제를 claim한다
   (2026-10-14 abe0b28e: 01:00 교체 → 다음 시도 10/15 01:00).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.workers import (
    generation_retry_policy,
    nightly_generation_batch,
    tasks,
    topic_swap_fallback,
)
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from tests.integration.test_topic_swap_fallback_postgres import (
    _exhausted_summary,
    _seed_hospital,
    _seed_item,
)

KST = ZoneInfo("Asia/Seoul")
TODAY = date(2026, 9, 16)
NOW = datetime(2026, 9, 16, 1, 0, 2, tzinfo=KST)  # 01:00 복구 스윕


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def no_incident_recovery(monkeypatch):
    async def _recover(_incident_id):
        return True

    monkeypatch.setattr(topic_swap_fallback, "_recover_incident_async", _recover)


def _freeze(monkeypatch, moment: datetime) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, nightly_generation_batch):
        monkeypatch.setattr(module, "datetime", _Frozen)


def _empty_slot(pg_conn, hospital_id, scheduled_date: date, sequence_no: int) -> uuid.UUID:
    return _seed_item(
        pg_conn,
        hospital_id,
        scheduled_date=scheduled_date,
        sequence_no=sequence_no,
        title=None,
        image_url=None,
        summary="{}",
    )


def test_the_cap_goes_to_overdue_and_tomorrow_before_three_days_out(
    pg_conn, pg_session, monkeypatch
):
    distant = _seed_hospital(pg_conn)
    urgent = _seed_hospital(pg_conn)
    far_ids = [
        _empty_slot(pg_conn, distant, TODAY + timedelta(days=3), index + 1) for index in range(3)
    ]
    # 이월 슬롯이라도 사흘 뒤면 오늘의 지난·내일 슬롯보다 뒤다.
    pg_conn.execute(
        text("UPDATE content_items SET carried_over_from = :d WHERE id = ANY(:ids)"),
        {"d": date(2026, 8, 31), "ids": far_ids},
    )
    overdue_id = _empty_slot(pg_conn, urgent, TODAY - timedelta(days=2), 1)
    tomorrow_id = _empty_slot(pg_conn, urgent, TODAY + timedelta(days=1), 2)
    monkeypatch.setattr(nightly_generation_batch, "NIGHTLY_GENERATION_CAP", 2)
    _freeze(monkeypatch, NOW)

    claimed, truncated, complete = tasks._load_nightly_generation_batch(
        pg_session,
        TODAY - timedelta(days=7),
        TODAY + timedelta(days=generation_retry_policy.generation_lookahead_days()),
    )

    assert [item.id for item in claimed] == [overdue_id, tomorrow_id]
    assert truncated == 3 and complete is True
    tokens = dict(
        pg_session.execute(
            select(ContentItem.id, ContentItem.generation_claim_token).where(
                ContentItem.id.in_([overdue_id, tomorrow_id, *far_ids])
            )
        ).all()
    )
    assert tokens[overdue_id] is not None and tokens[tomorrow_id] is not None
    assert all(tokens[item_id] is None for item_id in far_ids)


def test_a_swap_after_midnight_is_claimed_by_the_same_sweep(pg_conn, pg_session, monkeypatch):
    hospital_id = _seed_hospital(pg_conn)
    target_id = pg_conn.execute(
        text("SELECT id FROM ai_query_targets WHERE hospital_id=:h AND name=:n"),
        {"h": hospital_id, "n": "대장내시경 수면 여부"},
    ).scalar_one()
    # 옛 주제는 어제(KST) 하루 예산을 다 쓰고 작가 예산이 찼다 — 오늘 예산은 그대로다.
    summary = json.loads(_exhausted_summary(target_id))
    summary[GENERATION_ATTEMPT_KEY].update(
        {"attempt_period": "2026-09-15", "provider_attempt_count": 2}
    )
    slot = TODAY + timedelta(days=3)
    item_id = _seed_item(pg_conn, hospital_id, scheduled_date=slot, summary=json.dumps(summary))
    _freeze(monkeypatch, NOW)
    window = (TODAY - timedelta(days=7), TODAY + timedelta(days=3))

    report = topic_swap_fallback.swap_exhausted_topics(
        pg_session, window_start=window[0], window_end=window[1], now=NOW.astimezone(UTC)
    )

    assert report.swapped == 1
    row = pg_session.get(ContentItem, item_id)
    pg_session.refresh(row)
    attempt = row.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert attempt["reason"] == topic_swap_fallback.TOPIC_SWAPPED_REASON
    assert attempt["provider_attempt_count"] == 0
    assert datetime.fromisoformat(attempt["next_retry_at"]) == NOW

    claimed, _truncated, _complete = tasks._load_nightly_generation_batch(
        pg_session, *window, is_eligible=tasks._generation_retry_is_eligible(pg_session)
    )

    assert item_id in {item.id for item in claimed}
