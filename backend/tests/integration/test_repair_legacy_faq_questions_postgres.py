"""Real PostgreSQL proof for the bounded legacy FAQ punctuation repair."""

from __future__ import annotations

import copy
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.services.content_ai_review import candidate_review_coverage, candidate_sha256
from app.utils import repair_legacy_faq_questions as repair


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()


def _seed_random_allowlist(pg_session: Session, monkeypatch):
    targets = tuple(
        repair.AllowedRepair(
            content_id=str(uuid.uuid4()),
            hospital_slug=f"faq-repair-it-{uuid.uuid4().hex[:12]}",
            old_question=f"통합 테스트 질문 {index} 알려줘",
        )
        for index in range(3)
    )
    monkeypatch.setattr(repair, "ALLOWED_REPAIRS", targets)

    rows: dict[str, ContentItem] = {}
    for index, target in enumerate(targets):
        hospital = Hospital(
            id=uuid.uuid4(),
            name=f"FAQ 복구 통합 테스트 병원 {index}",
            slug=target.hospital_slug,
            status=HospitalStatus.ACTIVE,
            site_live=True,
        )
        schedule = ContentSchedule(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            plan="PLAN_12",
            publish_days=[1, 3],
            active_from=date(2026, 9, 1),
            is_active=True,
        )
        item = ContentItem(
            id=uuid.UUID(target.content_id),
            hospital_id=hospital.id,
            schedule_id=schedule.id,
            content_type=ContentType.FAQ,
            sequence_no=1,
            total_count=3,
            scheduled_date=date(2026, 9, 7),
            status=ContentStatus.PUBLISHED,
            content_revision=10 + index,
            title=f"그대로 남아야 하는 제목 {index}",
            body=f"그대로 남아야 하는 본문 {index}",
            meta_description=f"그대로 남아야 하는 설명 {index}",
            faq_question=target.old_question,
            faq_answer_summary=f"그대로 남아야 하는 답변 {index}",
            references_list=[
                {"title": "질병관리청", "url": f"https://www.kdca.go.kr/test/{index}"}
            ],
            published_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        pg_session.add_all([hospital, schedule, item])
        pg_session.flush()

        if index == 0:
            item.essence_check_summary = {
                "ai_review": {
                    "schema_version": 2,
                    "status": "PASS",
                    "candidate_sha256": candidate_sha256(item),
                    "coverage": candidate_review_coverage(item),
                    "findings": [],
                }
            }
        elif index == 1:
            item.essence_check_summary = {
                "blocking": True,
                "findings": ["기존 미해결 사실 확인"],
                "ai_review": {
                    "schema_version": 2,
                    "status": "REVISE",
                    "candidate_sha256": candidate_sha256(item),
                    "coverage": candidate_review_coverage(item),
                    "findings": [{"message": "기존 미해결 사실 확인"}],
                },
            }
        rows[target.content_id] = item

    pg_session.flush()
    return targets, rows


def _content_snapshot(pg_session: Session, targets) -> dict[str, dict]:
    items = pg_session.scalars(
        select(ContentItem).where(
            ContentItem.id.in_([uuid.UUID(target.content_id) for target in targets])
        )
    ).all()
    return {
        str(item.id): {
            "title": item.title,
            "body": item.body,
            "meta_description": item.meta_description,
            "faq_question": item.faq_question,
            "faq_answer_summary": item.faq_answer_summary,
            "references_list": copy.deepcopy(item.references_list),
            "status": item.status,
            "content_type": item.content_type,
            "content_revision": item.content_revision,
            "essence_check_summary": copy.deepcopy(item.essence_check_summary),
        }
        for item in items
    }


def _audit_count(pg_session: Session, targets) -> int:
    return int(
        pg_session.scalar(
            select(func.count(AdminAuditLog.id)).where(
                AdminAuditLog.action == "legacy_faq_question_punctuation_repaired",
                AdminAuditLog.target_id.in_([target.content_id for target in targets]),
            )
        )
        or 0
    )


def test_actual_postgres_apply_and_idempotent_replay_change_only_question_and_revision(
    pg_session, monkeypatch
) -> None:
    targets, _rows = _seed_random_allowlist(pg_session, monkeypatch)
    before = _content_snapshot(pg_session, targets)
    plan = repair.create_repair_plan(pg_session)

    first = repair.apply_repair_plan(pg_session, plan)

    assert first.repair_state == "APPLIED_REVIEW_DEFERRED"
    assert first.updated_count == 3
    assert first.public_ready is False
    after = _content_snapshot(pg_session, targets)
    for target in targets:
        content_id = target.content_id
        expected = dict(before[content_id])
        expected["faq_question"] = target.new_question
        expected["content_revision"] += 1
        assert after[content_id] == expected
    assert after[targets[1].content_id]["essence_check_summary"] == before[
        targets[1].content_id
    ]["essence_check_summary"]
    assert _audit_count(pg_session, targets) == 3

    replay = repair.apply_repair_plan(pg_session, plan)

    assert replay.repair_state == "ALREADY_APPLIED_REVIEW_DEFERRED"
    assert replay.updated_count == 0
    assert _content_snapshot(pg_session, targets) == after
    assert _audit_count(pg_session, targets) == 3


def test_one_stale_revision_aborts_all_rows_and_writes_no_audit(
    pg_session, monkeypatch
) -> None:
    targets, _rows = _seed_random_allowlist(pg_session, monkeypatch)
    plan = repair.create_repair_plan(pg_session)
    stale_id = uuid.UUID(targets[1].content_id)
    pg_session.execute(
        update(ContentItem)
        .where(ContentItem.id == stale_id)
        .values(content_revision=ContentItem.content_revision + 1)
    )
    pg_session.commit()
    before_apply = _content_snapshot(pg_session, targets)

    with pytest.raises(repair.RepairSafetyError, match="no changes applied"):
        repair.apply_repair_plan(pg_session, plan)

    assert _content_snapshot(pg_session, targets) == before_apply
    assert all(
        before_apply[target.content_id]["faq_question"] == target.old_question
        for target in targets
    )
    assert _audit_count(pg_session, targets) == 0
