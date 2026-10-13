"""범용 서술 각도(`treatment_narrative.source == 'fallback'`) 글의 승인 판(0090, 실제 PostgreSQL).

서술 각도는 작성 힌트이지 승인 근거가 아니다. 0090 함수는 `fallback`도 받되 비어 있지 않은
`angle`·객체 모양·승인 Essence 검사는 그대로다. 발행 게이트는 공개된 적 없는 범용 각도 글에
현재 본문 hash에 묶인 독립 검수를 요구하고, 없으면 STALE로 막아 복구 스윕이 재검수한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.content_ai_review import candidate_sha256
from app.services.content_publication import _blocking_ai_review_state
from app.services.content_revision_storage import reconcile_content_revisions
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, compute_sources_snapshot_hash
from app.workers.nightly_generation_batch import _needs_generation_recovery

pytestmark = pytest.mark.asyncio

TITLE = "도수치료 후 생활 관리"
_SQL_DIR = Path(__file__).resolve().parents[2] / "alembic" / "revision_sql"
FALLBACK_NARRATIVE = {
    "source": "fallback",
    "treatment": "도수치료",
    "angle": "증상, 진단, 치료 선택지, 회복 과정과 주의사항을 환자 언어로 설명합니다.",
    "details": {},
}


async def _seed(
    db,
    *,
    narrative: dict | None = None,
    philosophy_status: PhilosophyStatus = PhilosophyStatus.APPROVED,
    ai_review: dict | None = None,
) -> ContentItem:
    now = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)
    token = uuid.uuid4().hex[:10]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"범용 각도 병원 {token}",
        slug=f"fallback-narrative-{token}",
        address="서울시 강남구 테헤란로 1",
        phone="02-1234-5678",
        treatments=[{"name": "체외충격파"}],
        status=HospitalStatus.ACTIVE,
        site_built=True,
        site_live=True,
        schedule_set=True,
        region=["서울"],
        specialties=["재활의학과"],
        keywords=[],
        competitors=[],
    )
    db.add(hospital)
    await db.flush()
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="공식 진료 안내",
        raw_text="병원 진료 범위와 예약 전 확인 사항입니다.",
        content_hash=f"source-{token}",
        status=SourceStatus.PROCESSED,
        processed_at=now,
    )
    db.add(source)
    await db.flush()
    philosophy = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=philosophy_status,
        positioning_statement="공식 자료에 근거해 설명합니다.",
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        source_asset_ids=[str(source.id)],
        approved_at=now,
    )
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[1, 3],
        active_from=date(2026, 9, 1),
    )
    db.add_all([philosophy, schedule])
    await db.flush()
    summary: dict = {"generation_provenance": {"source_asset_ids": [str(source.id)]}}
    if ai_review is not None:
        summary["ai_review"] = ai_review
    item = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.NOTICE,
        sequence_no=1,
        total_count=12,
        title=TITLE,
        body="승인 기준에 맞춘 본문입니다.",
        meta_description="설명",
        references_list=[],
        reference_checks=[],
        scheduled_date=date(2026, 10, 13),
        status=ContentStatus.DRAFT,
        generated_at=now,
        essence_status=ESSENCE_STATUS_ALIGNED,
        content_philosophy_id=philosophy.id,
        generation_philosophy_id=philosophy.id,
        last_reviewed_philosophy_id=philosophy.id,
        content_revision=2,
        content_brief={
            "schema_version": "content-brief-v2",
            "target_query": "도수치료 후 관리",
            "treatment_narrative": narrative if narrative is not None else dict(FALLBACK_NARRATIVE),
            "source_snapshot": {"hash": token, "source_asset_ids": [str(source.id)]},
        },
        essence_check_summary=summary,
    )
    db.add(item)
    await db.flush()
    return item


async def _publish(db, item: ContentItem) -> int:
    item.status = ContentStatus.PUBLISHED
    item.published_at = datetime.now(UTC)
    item.published_by = "system:auto-publish"
    item.first_published_at = item.published_at
    item.first_published_by = item.published_by
    item.active_revision_id = None
    await db.flush()
    written = await reconcile_content_revisions(db, content_item_id=item.id)
    return written.created_count


def _bound_pass(item: ContentItem) -> dict:
    return {"status": "PASS", "schema_version": "3", "candidate_sha256": candidate_sha256(item)}


async def test_fallback_draft_with_bound_pass_passes_the_gate_and_gets_a_revision(pg_async_session):
    item = await _seed(pg_async_session)
    item.essence_check_summary = {**item.essence_check_summary, "ai_review": _bound_pass(item)}
    await pg_async_session.flush()

    assert _blocking_ai_review_state(item) is None
    assert await _publish(pg_async_session, item) == 1
    await pg_async_session.refresh(item, ["active_revision_id"])
    assert item.active_revision_id is not None


@pytest.mark.parametrize(
    "review",
    [
        None,
        {"status": "PASS", "schema_version": "3"},  # hash 없는 옛 기록
        {"status": "PASS", "schema_version": "3", "candidate_sha256": "0" * 64},  # 다른 본문
    ],
)
async def test_fallback_draft_without_bound_review_is_stale_and_swept(pg_async_session, review):
    item = await _seed(pg_async_session, ai_review=review)

    state = _blocking_ai_review_state(item)
    assert state is not None and state[0] == "STALE"
    if review is None or not review.get("candidate_sha256"):
        # 게이트가 막은 행은 복구 스윕이 집어 재검수한다 — 사람의 단계가 없다.
        selected = (
            await pg_async_session.execute(
                select(ContentItem.id).where(
                    ContentItem.id == item.id, _needs_generation_recovery()
                )
            )
        ).scalar_one_or_none()
        assert selected == item.id


async def test_bound_fallback_draft_is_not_swept_for_review(pg_async_session):
    item = await _seed(pg_async_session)
    item.essence_check_summary = {**item.essence_check_summary, "ai_review": _bound_pass(item)}
    item.image_url = "https://img.example/a.png"
    item.image_policy_verified_at = datetime(2026, 10, 13, tzinfo=UTC)
    await pg_async_session.flush()

    selected = (
        await pg_async_session.execute(
            select(ContentItem.id).where(ContentItem.id == item.id, _needs_generation_recovery())
        )
    ).scalar_one_or_none()
    assert selected is None


async def test_fallback_draft_without_approved_essence_gets_no_revision(pg_async_session):
    item = await _seed(pg_async_session, philosophy_status=PhilosophyStatus.ARCHIVED)
    item.essence_check_summary = {**item.essence_check_summary, "ai_review": _bound_pass(item)}

    assert await _publish(pg_async_session, item) == 0


async def test_fallback_draft_with_empty_angle_gets_no_revision(pg_async_session):
    item = await _seed(pg_async_session, narrative={**FALLBACK_NARRATIVE, "angle": "  "})
    item.essence_check_summary = {**item.essence_check_summary, "ai_review": _bound_pass(item)}

    assert await _publish(pg_async_session, item) == 0


async def test_unknown_narrative_source_still_gets_no_revision(pg_async_session):
    item = await _seed(pg_async_session, narrative={**FALLBACK_NARRATIVE, "source": "model_guess"})

    assert await _publish(pg_async_session, item) == 0


async def test_published_fallback_row_is_not_hidden_by_the_review_rule(pg_async_session):
    item = await _seed(pg_async_session)
    item.status = ContentStatus.PUBLISHED
    item.first_published_at = datetime.now(UTC)

    assert _blocking_ai_review_state(item) is None


async def test_0089_swap_rejects_and_0090_accepts_fallback(pg_async_session):
    """0089 함수로 되돌리면 범용 각도 글은 판이 없고, 0090으로 올리면 다시 생긴다."""

    connection = await pg_async_session.connection()
    item = await _seed(pg_async_session)
    await connection.exec_driver_sql(
        (_SQL_DIR / "reconcile_content_revisions_0089.sql").read_text(encoding="utf-8")
    )
    assert await _publish(pg_async_session, item) == 0

    await connection.exec_driver_sql(
        (_SQL_DIR / "reconcile_content_revisions_0090.sql").read_text(encoding="utf-8")
    )
    created = (
        await pg_async_session.execute(
            text("SELECT * FROM reconcile_content_revisions(:id)"), {"id": item.id}
        )
    ).one()
    assert created.created_count == 1
