"""상위 재검수 대상 선택 SQL을 실제 JSONB로 확인한다(Phase 1-10b)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.workers.post_publish_ai_review import _uncertain_settled_stmt


def _marker(**extra):
    return {
        "post_publish_ai_review": {
            "status": "FLAGGED",
            "checked_at": "2026-10-01T00:00:00+00:00",
            "structured_findings": [{"severity": "UNCERTAIN", "quote": "q", "message": "m"}],
            **extra,
        }
    }


def test_only_finished_flags_without_a_senior_rereview_are_selected(pg_conn):
    db = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
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

    def _post(sequence_no, summary):
        item = ContentItem(
            hospital_id=hospital.id, schedule_id=schedule.id, content_type=ContentType.DISEASE,
            sequence_no=sequence_no, total_count=12, scheduled_date=date(2026, 9, sequence_no),
            title="가상 글", body="본문", status=ContentStatus.PUBLISHED,
            published_at=datetime(2026, 9, sequence_no, tzinfo=UTC),
            essence_check_summary=summary,
        )
        db.add(item)
        db.flush()
        return item

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
        for item in db.execute(_uncertain_settled_stmt(50)).unique().scalars().all()
        if item.hospital_id == hospital.id
    }

    assert settled.id in selected
    assert still_correcting.id not in selected
    assert already_rereviewed.id not in selected
    db.close()
