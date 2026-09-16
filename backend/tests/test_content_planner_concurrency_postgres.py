"""Two actual database sessions cannot spend the same uncovered question twice."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_contract_backlog_postgres import seed

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.hospital import Hospital
from app.models.sov import AIQueryTarget
from app.services.content_target_planner import prepare_automatic_content_brief_sync


def test_parallel_writers_serialize_question_selection_until_the_brief_is_committed():
    url = make_url(
        os.environ.get(
            "SYNC_DATABASE_URL",
            "postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test",
        )
    )
    assert url.database == "reputation_test"
    engine = create_engine(url)
    first_planned, second_entered, release_first = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    hospital_id = None
    try:
        with Session(engine, expire_on_commit=False) as db:
            hospital, schedule, first = seed(db)
            hospital_id, first_id = hospital.id, first.id
            first.content_type = ContentType.FAQ
            second = ContentItem(
                hospital_id=hospital_id,
                schedule_id=schedule.id,
                content_type=ContentType.FAQ,
                sequence_no=2,
                total_count=12,
                scheduled_date=date(2026, 9, 3),
                status=ContentStatus.DRAFT,
            )
            db.add(second)
            for topic in ("위내시경", "대장내시경"):
                db.add(
                    AIQueryTarget(
                        hospital_id=hospital_id,
                        name=f"{topic} 검사는 어떻게 준비하나요?",
                        treatment=topic,
                        target_intent="정보 탐색",
                        priority="HIGH",
                        status="ACTIVE",
                        target_month="2026-09",
                    )
                )
            db.flush()
            second_id = second.id
            db.commit()

        def plan(item_id, hold=False):
            with Session(engine, expire_on_commit=False) as db:
                row = db.get(ContentItem, item_id)
                hospital = db.get(Hospital, hospital_id)
                if not hold:
                    second_entered.set()
                prepare_automatic_content_brief_sync(
                    db,
                    item=row,
                    hospital=hospital,
                    philosophy=SimpleNamespace(id=uuid4(), version=1),
                )
                if hold:
                    first_planned.set()
                    assert release_first.wait(10), "First writer was not released"
                db.commit()
                return row.query_target_id

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_run = pool.submit(plan, first_id, True)
            try:
                assert first_planned.wait(5), "First planning operation failed to start"
                second_run = pool.submit(plan, second_id)
                assert second_entered.wait(5)
                # Read lock evidence instead of relying only on a timing sleep.
                from sqlalchemy import text

                with engine.connect() as probe:
                    for _ in range(50):
                        waiting = probe.scalar(
                            text(
                                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
                            )
                        )
                        if waiting:
                            break
                        threading.Event().wait(0.02)
                assert waiting >= 1 and not second_run.done()
            finally:
                release_first.set()
            chosen = (first_run.result(timeout=10), second_run.result(timeout=10))
            assert all(chosen) and chosen[0] != chosen[1]
    finally:
        release_first.set()
        if hospital_id is not None:
            with Session(engine) as db:
                db.execute(delete(Hospital).where(Hospital.id == hospital_id))
                db.commit()
        engine.dispose()
