"""0091 승인 판 함수 — D36(참고자료 없이)·D37(검수 장애면 먼저 발행) (실제 PostgreSQL).

- D36: 참고자료 0개 의료 글도 판을 받는다(표지 없이도). 참고자료가 **있으면** 통과한 확인이 있어야 한다.
- D37: 독립 검수가 UNAVAILABLE인 글은 발행 경로가 같은 후보 hash로 적은 선발행 표지가 있을 때만 판을
  받는다. 표지가 없거나 다른 hash면 종전처럼 막는다.
- 공개 가시성도 같은 글을 숨기지 않는다. 함수는 트랜잭션 안에 0091을 설치해 본다(공유 테스트 DB의
  head와 무관하다). 내려가기(0090)는 D37 표지를 무시한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models.content import ContentItem, ContentStatus, ContentType
from app.services.content_ai_review import candidate_review_coverage, candidate_sha256
from app.services.content_publication import (
    AI_REVIEW_DEFERRED_KEY,
    ai_review_deferral_marker,
    ai_review_deferred,
    has_required_references,
)
from app.services.content_revision_storage import reconcile_content_revisions
from app.services.content_visibility import assess_public_visibility
from app.services.reference_publication import bind_reference_checks_to_revision
from app.services.reference_requirement import stamp_reference_requirement
from tests.integration.test_d23_optional_reference_revision_postgres import (
    MEDICAL_TITLE,
    _seed,
)

pytestmark = pytest.mark.asyncio

_SQL_DIR = Path(__file__).resolve().parents[2] / "alembic" / "revision_sql"


async def _install(db, version: str) -> None:
    connection = await db.connection()
    await connection.exec_driver_sql(
        (_SQL_DIR / f"reconcile_content_revisions_{version}.sql").read_text(encoding="utf-8")
    )


@pytest.fixture(autouse=True)
async def _function_0091(pg_async_session):
    await _install(pg_async_session, "0091")


def _mark_published(item: ContentItem) -> None:
    item.status = ContentStatus.PUBLISHED
    item.published_at = datetime.now(UTC)
    item.published_by = "system:auto-publish"
    item.first_published_at = item.published_at
    item.first_published_by = item.published_by
    item.active_revision_id = None


async def _created(db, item: ContentItem) -> int:
    await db.flush()
    return (await reconcile_content_revisions(db, content_item_id=item.id)).created_count


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


def _unavailable_review(item: ContentItem, *, sha: str | None = None) -> dict:
    return {
        "status": "UNAVAILABLE",
        "findings": [],
        "blocking": False,
        "schema_version": "content-review-v2",
        "candidate_sha256": sha or candidate_sha256(item),
        "coverage": candidate_review_coverage(item),
        "unavailable_reason": "PROVIDER_ERROR",
    }


async def test_medical_post_without_references_gets_a_revision_and_is_public(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.DISEASE, title=MEDICAL_TITLE)
    assert has_required_references(item)
    item.reference_checks = bind_reference_checks_to_revision(item)
    _mark_published(item)

    # 표지 없이도(0091은 참고자료 수를 보지 않는다).
    assert await _created(pg_async_session, item) == 1

    loaded = await _reload(pg_async_session, item)
    assert loaded.active_revision.references_list == []
    visibility = assess_public_visibility(loaded, loaded.content_philosophy_id)
    assert visibility.visible, visibility.blockers


async def test_present_references_still_need_a_passing_check(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.DISEASE, title=MEDICAL_TITLE)
    item.references_list = [{"title": "치핵", "url": "https://health.kdca.go.kr/x"}]
    item.reference_checks = []
    _mark_published(item)

    assert await _created(pg_async_session, item) == 0


async def test_unavailable_review_needs_the_hash_bound_deferral_marker(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.DISEASE, title=MEDICAL_TITLE)
    item.essence_check_summary = {
        **item.essence_check_summary,
        "ai_review": _unavailable_review(item),
    }
    assert ai_review_deferred(item)
    _mark_published(item)

    assert await _created(pg_async_session, item) == 0

    item.essence_check_summary = {
        **item.essence_check_summary,
        AI_REVIEW_DEFERRED_KEY: ai_review_deferral_marker(item),
    }
    assert await _created(pg_async_session, item) == 1


async def test_marker_for_another_candidate_is_ignored(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.DISEASE, title=MEDICAL_TITLE)
    item.essence_check_summary = {
        **item.essence_check_summary,
        "ai_review": _unavailable_review(item),
        AI_REVIEW_DEFERRED_KEY: {"candidate_sha256": "another-candidate"},
    }
    _mark_published(item)

    assert await _created(pg_async_session, item) == 0


async def test_downgrade_to_0090_ignores_the_deferral_marker(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.DISEASE, title=MEDICAL_TITLE)
    item.essence_check_summary = {
        **item.essence_check_summary,
        "ai_review": _unavailable_review(item),
    }
    item.essence_check_summary = {
        **item.essence_check_summary,
        AI_REVIEW_DEFERRED_KEY: ai_review_deferral_marker(item),
    }
    _mark_published(item)
    stamp_reference_requirement(item)
    await _install(pg_async_session, "0090")

    assert await _created(pg_async_session, item) == 0

    await _install(pg_async_session, "0091")
    created = (
        await pg_async_session.execute(
            text("SELECT * FROM reconcile_content_revisions(:id)"), {"id": item.id}
        )
    ).one()
    assert created.created_count == 1


async def test_review_unavailable_published_post_stays_visible(pg_async_session):
    item = await _seed(pg_async_session, content_type=ContentType.DISEASE, title=MEDICAL_TITLE)
    item.essence_check_summary = {
        **item.essence_check_summary,
        "ai_review": _unavailable_review(item),
    }
    item.essence_check_summary = {
        **item.essence_check_summary,
        AI_REVIEW_DEFERRED_KEY: ai_review_deferral_marker(item),
    }
    item.reference_checks = bind_reference_checks_to_revision(item)
    _mark_published(item)
    assert await _created(pg_async_session, item) == 1

    loaded = await _reload(pg_async_session, item)
    visibility = assess_public_visibility(loaded, loaded.content_philosophy_id)
    assert visibility.visible, visibility.blockers
