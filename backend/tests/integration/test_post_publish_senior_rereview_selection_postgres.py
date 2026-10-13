"""상위 재검수 대상 선택 SQL을 실제 JSONB로 확인한다(Phase 1-10b)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.workers.post_publish_ai_review import _uncertain_settled_stmt

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
        for item in db.execute(_uncertain_settled_stmt(50, now=NOW, days=3)).unique().scalars().all()
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
        for item in db.execute(_uncertain_settled_stmt(limit, now=NOW, days=3)).unique().scalars()
    ]

    assert eligible.id in selected
    for excluded in (mixed, fresh, malformed, not_a_list):
        assert excluded.id not in selected
    db.close()
