"""SQL claim 술어의 모양 — `generation_claim_is_active`의 정확한 부정·그 자체(DB 없이 컴파일만 본다).

실제 행 선택은 `tests/integration/test_generation_claim_predicates_postgres.py`가 Postgres로 본다.
"""

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.content import ContentItem
from app.workers import nightly_generation_batch, published_image_refresh, topic_swap_fallback

CUTOFF = datetime(2026, 9, 15, 23, 0, tzinfo=UTC)


def _sql(clause) -> str:
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_the_claimable_filter_is_the_exact_negation_of_a_live_claim():
    assert _sql(nightly_generation_batch._nightly_generation_claim_filter(CUTOFF)) == (
        "content_items.generation_claim_token IS NULL "
        "OR content_items.generation_claimed_at IS NULL "
        "OR content_items.generation_claimed_at < '2026-09-15 23:00:00+00:00'"
    )
    assert _sql(nightly_generation_batch._live_generation_claim_predicate(CUTOFF)) == (
        "content_items.generation_claim_token IS NOT NULL "
        "AND content_items.generation_claimed_at IS NOT NULL "
        "AND content_items.generation_claimed_at >= '2026-09-15 23:00:00+00:00'"
    )


def test_every_claim_loader_uses_the_same_claimable_filter():
    claimable = _sql(nightly_generation_batch._nightly_generation_claim_filter(CUTOFF))
    loader = _sql(
        nightly_generation_batch._nightly_generation_stmt(date(2026, 9, 9), date(2026, 9, 18), CUTOFF)
    )
    swap = _sql(select(ContentItem.id).where(topic_swap_fallback._inactive_claim_filter(CUTOFF)))
    assert claimable in loader
    assert claimable in swap
    # 백로그 복구는 로더와 같은 함수를 import해 쓴다.
    from app.workers import content_backlog_recovery

    assert content_backlog_recovery._nightly_generation_claim_filter is (
        nightly_generation_batch._nightly_generation_claim_filter
    )
    assert published_image_refresh._nightly_generation_claim_filter is (
        nightly_generation_batch._nightly_generation_claim_filter
    )


def test_stuck_claims_count_only_live_claims():
    stuck = _sql(
        nightly_generation_batch._stuck_claims_stmt(
            date(2026, 9, 9), date(2026, 9, 18), claim_cutoff=CUTOFF
        )
    )
    assert _sql(nightly_generation_batch._live_generation_claim_predicate(CUTOFF)) in stuck
