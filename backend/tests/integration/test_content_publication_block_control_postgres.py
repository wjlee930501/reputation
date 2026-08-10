"""Real-Postgres proof for the publication-block retry run."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.workers.content_publication_block_control import ensure_publication_block_run


def test_publication_block_creates_one_retryable_run_per_content_revision(pg_conn) -> None:
    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    item_id = uuid.uuid4()
    pg_conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status, site_live) "
            "VALUES (:id, '자동복구검증병원', :slug, 'ACTIVE', true)"
        ),
        {"id": hospital_id, "slug": f"recovery-{uuid.uuid4().hex[:8]}"},
    )
    pg_conn.execute(
        text(
            "INSERT INTO content_schedules "
            "(id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hospital_id, 'PLAN_8', '[1, 3]', :active_from)"
        ),
        {"id": schedule_id, "hospital_id": hospital_id, "active_from": date(2026, 8, 1)},
    )
    pg_conn.execute(
        text(
            "INSERT INTO content_items "
            "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
            "scheduled_date, status, title, body) "
            "VALUES (:id, :hospital_id, :schedule_id, 'FAQ', 1, 8, :scheduled_date, "
            "'DRAFT', '검증 제목', '검증 본문')"
        ),
        {
            "id": item_id,
            "hospital_id": hospital_id,
            "schedule_id": schedule_id,
            "scheduled_date": date(2026, 8, 11),
        },
    )

    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        hospital = session.get(Hospital, hospital_id)
        item = session.get(ContentItem, item_id)
        assert hospital is not None and item is not None

        first = ensure_publication_block_run(
            session,
            item=item,
            hospital=hospital,
            code="CONTENT_IMAGE_NOT_READY",
            message="대표 이미지가 아직 준비되지 않았습니다.",
        )
        session.commit()
        replay = ensure_publication_block_run(
            session,
            item=item,
            hospital=hospital,
            code="CONTENT_IMAGE_NOT_READY",
            message="대표 이미지가 아직 준비되지 않았습니다.",
        )

        assert replay.id == first.id
        assert first.state == OperationRunState.FAILED
        assert first.operation_type == "REGENERATE_CONTENT_IMAGE"
        assert first.request_payload["_dispatch"]["target_id"] == str(item_id)
        count = session.scalar(
            select(func.count()).select_from(OperationRun).where(
                OperationRun.hospital_id == hospital_id,
                OperationRun.safe_error_code == "CONTENT_IMAGE_NOT_READY",
            )
        )
        assert count == 1
    finally:
        session.close()
