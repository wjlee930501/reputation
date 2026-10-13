"""D23 진료비·병원 선택 글 — 참고자료 0개여도 승인 판이 생기고 공개된다(0089, 실제 PostgreSQL).

발행 경로는 `references_block_when_empty` 판정을 `essence_check_summary.reference_requirement`에
제목 hash·유형과 함께 적고, `reconcile_content_revisions`는 그 표지만 읽는다. 의료 글·제목이 바뀐
표지·표지 없는 글은 종전처럼 판을 받지 못한다. NOTICE 규칙은 그대로다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.content_publication import has_required_references
from app.services.content_revision_storage import reconcile_content_revisions
from app.services.content_visibility import assess_public_visibility
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, compute_sources_snapshot_hash
from app.services.reference_publication import bind_reference_checks_to_revision
from app.services.reference_requirement import (
    REFERENCE_REQUIREMENT_SUMMARY_KEY,
    stamp_reference_requirement,
)

pytestmark = pytest.mark.asyncio

COST_TITLE = "강남 도수치료 비용, 무엇으로 정해지나요?"
MEDICAL_TITLE = "치핵 증상과 진료 흐름"
_SQL_DIR = Path(__file__).resolve().parents[2] / "alembic" / "revision_sql"


async def _seed(db, *, content_type: ContentType, title: str) -> ContentItem:
    now = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)
    token = uuid.uuid4().hex[:10]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"D23 병원 {token}",
        slug=f"d23-optional-{token}",
        address="서울시 강남구 테헤란로 1",
        phone="02-1234-5678",
        treatments=[{"name": "도수치료"}],
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
        status=PhilosophyStatus.APPROVED,
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
    item = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=content_type,
        sequence_no=1,
        total_count=12,
        title=title,
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
            "target_query": "도수치료 비용",
            "treatment_narrative": {
                "source": "approved_philosophy",
                "angle": "공식 자료에 근거한 안내",
            },
            "source_snapshot": {"hash": token, "source_asset_ids": [str(source.id)]},
        },
        essence_check_summary={"generation_provenance": {"source_asset_ids": [str(source.id)]}},
    )
    db.add(item)
    await db.flush()
    return item


async def _publish(db, item: ContentItem, *, stamp: bool = True) -> int:
    """자동 발행·관리자 발행과 같은 순서: 근거 고정 → PUBLISHED → 표지 → flush → 판 SQL."""

    bound = bind_reference_checks_to_revision(item)
    assert bound == []
    item.reference_checks = bound
    item.status = ContentStatus.PUBLISHED
    item.published_at = datetime.now(UTC)
    item.published_by = "system:auto-publish"
    item.first_published_at = item.published_at
    item.first_published_by = item.published_by
    item.active_revision_id = None
    if stamp:
        stamp_reference_requirement(item)
    await db.flush()
    written = await reconcile_content_revisions(db, content_item_id=item.id)
    return written.created_count


async def _reload(db, item: ContentItem) -> ContentItem:
    item_id = item.id
    db.expire(item)
    return (
        await db.execute(
            select(ContentItem)
            .options(selectinload(ContentItem.active_revision))
            .where(ContentItem.id == item_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def test_cost_title_column_with_no_references_gets_revision_and_is_public(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.COLUMN, title=COST_TITLE)
    assert has_required_references(item)

    assert await _publish(pg_async_session, item) == 1

    loaded = await _reload(pg_async_session, item)
    assert loaded.active_revision_id is not None
    assert loaded.active_revision.references_list == []
    marker = loaded.essence_check_summary[REFERENCE_REQUIREMENT_SUMMARY_KEY]
    assert marker["block_when_empty"] is False
    assert marker["no_source_topic"] == "COST"
    assert marker["content_type"] == "COLUMN"
    visibility = assess_public_visibility(loaded, loaded.content_philosophy_id)
    assert visibility.visible, visibility.blockers


async def test_cost_title_without_marker_keeps_the_strict_sql_rule(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.COLUMN, title=COST_TITLE)

    assert await _publish(pg_async_session, item, stamp=False) == 0


async def test_medical_post_with_no_references_gets_no_revision(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.DISEASE, title=MEDICAL_TITLE)
    # 발행 게이트도 막는다 — 이 SQL 검사는 마지막 방어선이다.
    assert not has_required_references(item)
    item.status = ContentStatus.PUBLISHED
    item.published_at = datetime.now(UTC)
    item.published_by = "system:auto-publish"
    stamp_reference_requirement(item)
    assert item.essence_check_summary[REFERENCE_REQUIREMENT_SUMMARY_KEY]["block_when_empty"]
    await pg_async_session.flush()

    written = await reconcile_content_revisions(pg_async_session, content_item_id=item.id)

    assert written.created_count == 0


async def test_marker_written_for_another_title_is_ignored(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.COLUMN, title=COST_TITLE)
    stamp_reference_requirement(item)
    # 표지를 적은 뒤 제목이 의료 글로 바뀌었다 — 옛 표지가 판을 열어 주면 안 된다.
    item.title = MEDICAL_TITLE
    item.status = ContentStatus.PUBLISHED
    item.published_at = datetime.now(UTC)
    item.published_by = "system:auto-publish"
    await pg_async_session.flush()

    written = await reconcile_content_revisions(pg_async_session, content_item_id=item.id)

    assert written.created_count == 0


async def test_marker_for_another_content_type_is_ignored(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.COLUMN, title=COST_TITLE)
    stamp_reference_requirement(item)
    item.essence_check_summary = {
        **item.essence_check_summary,
        REFERENCE_REQUIREMENT_SUMMARY_KEY: {
            **item.essence_check_summary[REFERENCE_REQUIREMENT_SUMMARY_KEY],
            "content_type": "NOTICE",
        },
    }
    item.status = ContentStatus.PUBLISHED
    item.published_at = datetime.now(UTC)
    item.published_by = "system:auto-publish"
    await pg_async_session.flush()

    written = await reconcile_content_revisions(pg_async_session, content_item_id=item.id)

    assert written.created_count == 0


async def test_notice_without_references_rule_is_unchanged(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.NOTICE, title="진료 시간 변경 안내")

    assert await _publish(pg_async_session, item, stamp=False) == 1


async def test_downgrade_function_restores_the_strict_clause(pg_async_session):
    """0086 함수로 되돌리면 표지가 있어도 판이 없고, 0089로 올리면 다시 생긴다(같은 트랜잭션)."""

    connection = await pg_async_session.connection()
    item = await _seed(pg_async_session, content_type=ContentType.COLUMN, title=COST_TITLE)
    await connection.exec_driver_sql(
        (_SQL_DIR / "reconcile_content_revisions_0086.sql").read_text(encoding="utf-8")
    )
    assert await _publish(pg_async_session, item) == 0

    await connection.exec_driver_sql(
        (_SQL_DIR / "reconcile_content_revisions_0089.sql").read_text(encoding="utf-8")
    )
    created = (
        await pg_async_session.execute(
            text("SELECT * FROM reconcile_content_revisions(:id)"), {"id": item.id}
        )
    ).one()
    assert created.created_count == 1
