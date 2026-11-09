"""주력 주제가 실제 DB에서 글 주제 선택과 목표 편수 집계를 움직이는가."""

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
from tests.db_env import require_db_url


def test_focus_topic_steers_open_slots_until_the_month_quota_is_met():
    url = make_url(require_db_url("SYNC_DATABASE_URL"))
    assert (url.database or "").endswith("_test"), "Only a *_test database may be used"
    engine = create_engine(url)
    hospital_id = None
    try:
        with Session(engine, expire_on_commit=False) as db:
            hospital, schedule, first = seed(db)
            hospital_id = hospital.id
            hospital.region = ["하남시"]
            hospital.content_focus_topics = {
                "version": 1,
                "topics": ["내시경"],
                "share": 50,
                "from_month": "2026-09",
                "until_month": "2026-09",
            }
            first.content_type = ContentType.DISEASE
            second = ContentItem(
                hospital_id=hospital_id,
                schedule_id=schedule.id,
                content_type=ContentType.DISEASE,
                sequence_no=2,
                total_count=12,
                scheduled_date=date(2026, 9, 3),
                status=ContentStatus.DRAFT,
            )
            cancelled = ContentItem(
                hospital_id=hospital_id,
                schedule_id=schedule.id,
                content_type=ContentType.DISEASE,
                sequence_no=3,
                total_count=12,
                scheduled_date=date(2026, 9, 5),
                status=ContentStatus.CANCELLED,
            )
            db.add_all([second, cancelled])
            targets = {
                "liver": AIQueryTarget(
                    hospital_id=hospital_id,
                    name="간질환 진료를 받으려는데 하남시 어느 병원으로 가야 해?",
                    condition_or_symptom="간질환",
                    target_intent="추천 탐색",
                    priority="HIGH",
                    status="ACTIVE",
                ),
                "scope": AIQueryTarget(
                    hospital_id=hospital_id,
                    name="위내시경 받으려는데 하남시 어느 병원으로 가야 해?",
                    condition_or_symptom="위내시경",
                    target_intent="추천 탐색",
                    priority="HIGH",
                    status="ACTIVE",
                ),
                "bad": AIQueryTarget(
                    hospital_id=hospital_id,
                    name="하남 진료를 받으려는데 하남시 어느 병원으로 가야 해?",
                    condition_or_symptom="하남",
                    target_intent="추천 탐색",
                    priority="HIGH",
                    status="ACTIVE",
                ),
            }
            db.add_all(targets.values())
            db.flush()
            ids = {key: target.id for key, target in targets.items()}
            first_id, second_id = first.id, second.id
            db.commit()

        def plan(item_id):
            with Session(engine, expire_on_commit=False) as db:
                row = db.get(ContentItem, item_id)
                hospital = db.get(Hospital, hospital_id)
                prepare_automatic_content_brief_sync(
                    db,
                    item=row,
                    hospital=hospital,
                    philosophy=SimpleNamespace(id=uuid4(), version=1),
                )
                db.commit()
                return row.query_target_id, row.content_focus_topic

        # 살아 있는 글 2편(취소 제외) × 50% = 1편. 첫 글은 주력 주제로 간다.
        assert plan(first_id) == (ids["scope"], "내시경")
        # 목표를 채웠으니 둘째 글은 평소 순서대로 고른다. 지역명 질문은 고르지 않는다.
        target_id, topic = plan(second_id)
        assert target_id == ids["liver"]
        assert topic is None
    finally:
        if hospital_id is not None:
            with Session(engine) as db:
                db.execute(delete(Hospital).where(Hospital.id == hospital_id))
                db.commit()
        engine.dispose()
