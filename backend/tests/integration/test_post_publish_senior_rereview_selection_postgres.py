"""상위 재검수 대상 선택 SQL을 실제 JSONB로 확인한다(Phase 1-10b)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.workers import post_publish_ai_review as sweep
from app.workers.post_publish_ai_review import _uncertain_settled_items

NOW = datetime(2026, 10, 13, tzinfo=UTC)


def _marker(**extra):
    return {
        "post_publish_ai_review": {
            "status": "FLAGGED",
            "checked_at": "2026-10-01T00:00:00+00:00",
            "structured_findings": [{"severity": "UNCERTAIN", "quote": "q", "message": "m"}],
            **extra,
        }
    }


def _hospital_and_schedule(db):
    hospital = Hospital(
        name="상위재검수가상의원",
        slug=f"senior-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
        site_live=True,
    )
    db.add(hospital)
    db.flush()
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[1], active_from=date(2026, 9, 1)
    )
    db.add(schedule)
    db.flush()
    return hospital, schedule


def _poster(db, hospital, schedule):
    def _post(sequence_no, summary, published_at=None):
        item = ContentItem(
            hospital_id=hospital.id, schedule_id=schedule.id, content_type=ContentType.DISEASE,
            sequence_no=sequence_no, total_count=12, scheduled_date=date(2026, 9, sequence_no),
            title="가상 글", body="본문", status=ContentStatus.PUBLISHED,
            published_at=published_at or datetime(2026, 9, sequence_no, tzinfo=UTC),
            essence_check_summary=summary,
        )
        db.add(item)
        db.flush()
        return item

    return _post


def test_only_finished_flags_without_a_senior_rereview_are_selected(pg_conn):
    db = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    hospital, schedule = _hospital_and_schedule(db)
    _post = _poster(db, hospital, schedule)

    settled = _post(1, _marker(correction={"finished": True, "passes": 2}))
    still_correcting = _post(2, _marker(correction={"finished": False, "passes": 1}))
    already_rereviewed = _post(
        3,
        _marker(
            correction={"finished": True, "passes": 2},
            senior_rereview={"at": "2026-10-08T00:00:00+00:00", "model": "m"},
        ),
    )

    selected = {
        item.id
        for item in _uncertain_settled_items(db, 50, now=NOW, days=3).items
        if item.hospital_id == hospital.id
    }

    assert settled.id in selected
    assert still_correcting.id not in selected
    assert already_rereviewed.id not in selected
    db.close()


def test_older_hard_flags_cannot_starve_an_eligible_uncertain_flag(pg_conn):
    """UNCERTAIN만 남았고 충분히 묵은 조건을 LIMIT 앞 SQL이 건다(Codex HOLD P2).

    예전에는 교정이 끝난 FLAGGED를 오래된 순으로 LIMIT한 뒤 파이썬이 UNCERTAIN을 걸러, 오래된
    HARD 글이 상한을 다 차지하면 UNCERTAIN 글은 영영 다시 보지 못했다.
    """
    db = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    hospital, schedule = _hospital_and_schedule(db)
    _post = _poster(db, hospital, schedule)
    finished = {"correction": {"finished": True, "passes": 2}}
    hard = [{"severity": "HARD", "quote": "q", "message": "m"}]
    limit = 3
    for n in range(1, limit + 1):
        _post(n, _marker(structured_findings=hard, **finished), datetime(1999, 1, n, tzinfo=UTC))
    mixed = _post(
        5,
        _marker(
            structured_findings=[*hard, {"severity": "UNCERTAIN", "quote": "q", "message": "m"}],
            **finished,
        ),
        datetime(1999, 2, 1, tzinfo=UTC),
    )
    fresh = _post(
        6,
        _marker(checked_at=(NOW - timedelta(days=1)).isoformat(), **finished),
        datetime(1999, 3, 1, tzinfo=UTC),
    )
    malformed = _post(7, _marker(checked_at="어제", **finished), datetime(1999, 4, 1, tzinfo=UTC))
    not_a_list = _post(
        8, _marker(structured_findings={"severity": "UNCERTAIN"}, **finished),
        datetime(1999, 5, 1, tzinfo=UTC),
    )
    eligible = _post(9, _marker(**finished), datetime(2000, 1, 1, tzinfo=UTC))

    selected = [
        item.id
        for item in _uncertain_settled_items(db, limit, now=NOW, days=3).items
    ]

    assert eligible.id in selected
    for excluded in (mixed, fresh, malformed, not_a_list):
        assert excluded.id not in selected
    db.close()


def test_an_impossible_checked_at_date_does_not_break_the_selection(pg_conn):
    """정규식은 통과하지만 달력에 없는 시각(2월 30일·13월)이 SQL 형 변환으로 스윕 전체를 깨지 않는다(R2-3)."""
    db = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    hospital, schedule = _hospital_and_schedule(db)
    _post = _poster(db, hospital, schedule)
    finished = {"correction": {"finished": True, "passes": 2}}
    feb_30 = _post(1, _marker(checked_at="2026-02-30T00:00:00+00:00", **finished))
    month_13 = _post(2, _marker(checked_at="2026-13-01T00:00:00+00:00", **finished))
    eligible = _post(3, _marker(**finished))

    selected = [item.id for item in _uncertain_settled_items(db, 50, now=NOW, days=3).items]

    assert eligible.id in selected
    assert feb_30.id not in selected and month_13.id not in selected
    db.close()


def test_young_uncertain_flags_cannot_starve_a_settled_one_across_pages(pg_conn, monkeypatch):
    """아직 묵지 않은 UNCERTAIN 글이 앞쪽 페이지를 다 채워도 뒤쪽의 묵은 글을 찾는다(키셋 페이지)."""
    monkeypatch.setattr(sweep, "_UNCERTAIN_SCAN_PAGE_SIZE", 2)
    db = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    hospital, schedule = _hospital_and_schedule(db)
    finished = {"correction": {"finished": True, "passes": 2}}
    young = (NOW - timedelta(days=1)).isoformat()

    def _post(sequence_no, item_id, summary):
        item = ContentItem(
            id=item_id, hospital_id=hospital.id, schedule_id=schedule.id,
            content_type=ContentType.DISEASE, sequence_no=sequence_no, total_count=12,
            scheduled_date=date(2026, 9, sequence_no), title="가상 글", body="본문",
            status=ContentStatus.PUBLISHED, published_at=datetime(2026, 9, sequence_no, tzinfo=UTC),
            essence_check_summary=summary,
        )
        db.add(item)
        db.flush()
        return item

    # 키셋은 id 순서다 — 묵지 않은 글이 가장 앞 id들을 차지한다.
    youngs = [
        _post(n, uuid.UUID(int=n), _marker(checked_at=young, **finished)) for n in range(1, 6)
    ]
    eligible = _post(9, uuid.UUID(int=2**128 - 1), _marker(**finished))

    selected = [item.id for item in _uncertain_settled_items(db, 50, now=NOW, days=3).items]

    assert eligible.id in selected
    assert not {item.id for item in youngs} & set(selected)
    db.close()


def _post_with_id(db, hospital, schedule):
    def _post(sequence_no, item_id, summary):
        return ContentItem(
            id=item_id, hospital_id=hospital.id, schedule_id=schedule.id,
            content_type=ContentType.DISEASE, sequence_no=sequence_no, total_count=12,
            scheduled_date=date(2026, 9, 1), title="가상 글", body="본문",
            status=ContentStatus.PUBLISHED, published_at=datetime(2026, 9, 1, tzinfo=UTC),
            essence_check_summary=summary,
        )

    return _post


def test_epoch_bearing_flags_are_selected_in_sql_past_an_unsettled_backlog(pg_conn, monkeypatch):
    """새 표시는 숫자 `checked_at_epoch`를 함께 쓴다 — 묵음 판정을 SQL이 걸어 앞쪽 백로그와 무관하다(R3-2)."""
    monkeypatch.setattr(sweep, "_UNCERTAIN_SCAN_PAGE_SIZE", 2)
    monkeypatch.setattr(sweep, "_UNCERTAIN_SCAN_MAX_PAGES", 1)
    db = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    hospital, schedule = _hospital_and_schedule(db)
    _post = _post_with_id(db, hospital, schedule)
    finished = {"correction": {"finished": True, "passes": 2}}
    settled_epoch = int((NOW - timedelta(days=10)).timestamp())
    young_epoch = int((NOW - timedelta(days=1)).timestamp())
    backlog = [_post(n, uuid.UUID(int=n), _marker(checked_at="어제", **finished)) for n in range(1, 6)]
    eligible = _post(
        8, uuid.UUID(int=2**128 - 1), _marker(checked_at_epoch=settled_epoch, **finished)
    )
    # 숫자 시각이 있으면 그것이 정본이다 — ISO 문자열이 오래돼 보여도 아직 묵지 않았다.
    young = _post(
        9, uuid.UUID(int=2**128 - 2),
        _marker(checked_at_epoch=young_epoch, **finished),
    )
    db.add_all([*backlog, eligible, young])
    db.flush()

    selected = {item.id for item in _uncertain_settled_items(db, 50, now=NOW, days=3).items}

    assert eligible.id in selected
    assert young.id not in selected
    assert not {item.id for item in backlog} & selected
    db.close()


def test_a_persisted_cursor_reaches_an_eligible_flag_behind_a_thousand_malformed_ones(pg_conn):
    """시각을 읽을 수 없는 옛 표시 1,000건이 앞을 막아도 커서를 tick 사이에 이어 결국 닿는다(R3-2)."""
    db = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    hospital, schedule = _hospital_and_schedule(db)
    _post = _post_with_id(db, hospital, schedule)
    finished = {"correction": {"finished": True, "passes": 2}}
    backlog = sweep._UNCERTAIN_SCAN_PAGE_SIZE * sweep._UNCERTAIN_SCAN_MAX_PAGES
    db.add_all(
        [
            _post(n, uuid.UUID(int=n), _marker(checked_at="어제", **finished))
            for n in range(1, backlog + 1)
        ]
    )
    eligible = _post(backlog + 1, uuid.UUID(int=2**128 - 1), _marker(**finished))
    db.add(eligible)
    db.flush()

    cursor = None
    found_at = None
    for tick in range(1, 4):
        selection = _uncertain_settled_items(db, 50, now=NOW, days=3, after_id=cursor)
        if eligible.id in {item.id for item in selection.items}:
            found_at = tick
            break
        cursor = selection.next_cursor

    assert found_at == 2  # 한 tick이 1,000건을 읽고, 다음 tick이 그 뒤에서 이어 읽는다
    db.close()


def test_the_cursor_wraps_to_the_beginning_after_the_last_row(pg_conn):
    db = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    hospital, schedule = _hospital_and_schedule(db)
    _post = _post_with_id(db, hospital, schedule)
    finished = {"correction": {"finished": True, "passes": 2}}
    eligible = _post(1, uuid.UUID(int=1), _marker(**finished))
    db.add(eligible)
    db.flush()

    past_the_end = _uncertain_settled_items(
        db, 50, now=NOW, days=3, after_id=uuid.UUID(int=2**128 - 1)
    )
    assert past_the_end.next_cursor is None  # 끝에 닿았다 — 다음 tick은 처음부터
    restarted = _uncertain_settled_items(
        db, 50, now=NOW, days=3, after_id=past_the_end.next_cursor
    )
    assert eligible.id in {item.id for item in restarted.items}
    db.close()
