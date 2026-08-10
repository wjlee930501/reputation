"""Naver source handoff orchestration with per-URL durable outcomes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import OperationRun
from app.services.asset_extractor import (
    fetch_naver_blog_post_urls,
    fetch_url_text,
    naver_blog_id_from,
    naver_blog_post_hash,
    naver_blog_post_identity,
)
from app.services.naver_handoff_contracts import (
    NaverHandoffItem,
    NaverHandoffResult,
    NaverHandoffState,
    pending_item,
)
from app.services.naver_handoff_incidents import (
    NaverIncidentContext,
    mark_naver_retrying,
    record_naver_failure,
    record_naver_recovery,
)
from app.services.naver_handoff_runs import (
    NaverFailedItemLookup,
    NaverRetryConflict,
    NaverRunStart,
    failed_parent_item,
    finish_naver_run,
    save_naver_items,
    start_naver_run,
)
from app.services.naver_handoff_sources import (
    NaverHospitalRef,
    NaverSourceProcess,
    process_naver_item,
)


@dataclass(frozen=True, slots=True)
class NaverCrawlOptions:
    blog_ref: str | None = None
    max_posts: int = 15
    operator_note: str | None = None
    created_by: str = "NAVER_WEEKLY_SYNC"
    actor: str = "네이버 자료 자동 수집"


@dataclass(frozen=True, slots=True)
class NaverRetryRequest:
    hospital: NaverHospitalRef
    parent_run_id: uuid.UUID
    url_hash: str
    actor: str


async def sync_hospital_naver_sources(
    db: AsyncSession, hospital: NaverHospitalRef, options: NaverCrawlOptions = NaverCrawlOptions()
) -> NaverHandoffResult:
    """Persist PENDING first, then save every discovered URL outcome independently."""
    blog_ref = (options.blog_ref or hospital.blog_url or "").strip()
    blog_id = naver_blog_id_from(blog_ref)
    if blog_id is None:
        run = await start_naver_run(
            db, NaverRunStart(hospital.id, None, (), "NAVER_SOURCE_HANDOFF")
        )
        error = "네이버 블로그 주소를 인식하지 못했습니다. 주소를 확인해 주세요."
        await finish_naver_run(db, run, (), run_error=("NAVER_BLOG_INVALID", error))
        return NaverHandoffResult(blog_id=None, run_id=run.id, error=error)

    post_urls, enum_error = await fetch_naver_blog_post_urls(blog_ref, options.max_posts)
    items = tuple(pending_item(url) for url in _unique_post_urls(post_urls))
    run = await start_naver_run(
        db, NaverRunStart(hospital.id, blog_id, items, "NAVER_SOURCE_HANDOFF")
    )
    if enum_error:
        message = "네이버 글 목록을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요."
        await finish_naver_run(db, run, items, run_error=("NAVER_RSS_FAILED", message))
        return NaverHandoffResult(blog_id=blog_id, run_id=run.id, error=message)

    outcomes = list(items)
    process = NaverSourceProcess(
        db, hospital, run.id, options.operator_note, options.created_by, options.actor
    )
    for index, item in enumerate(items):
        outcomes[index] = await process_naver_item(process, item, fetch_url_text)
        await save_naver_items(db, run, tuple(outcomes))
    final_items = tuple(outcomes)
    await finish_naver_run(db, run, final_items)
    return _result(blog_id, run, final_items)


async def retry_failed_naver_source(
    db: AsyncSession, request: NaverRetryRequest
) -> NaverHandoffResult:
    """Retry one failed URL and keep successful sibling assets untouched."""
    parent, failed = await failed_parent_item(
        db,
        NaverFailedItemLookup(
            request.hospital.id, request.parent_run_id, request.url_hash
        ),
    )
    retrying = await mark_naver_retrying(
        db,
        NaverIncidentContext(
            request.hospital.id,
            request.hospital.name,
            parent.id,
            failed,
            request.actor,
        ),
    )
    if retrying is None:
        raise NaverRetryConflict(
            "NAVER_RETRY_ALREADY_CLAIMED",
            "이 글은 이미 다시 수집 중이거나 복구되었습니다. 화면을 새로고침해 현재 상태를 확인해 주세요.",
        )
    pending = pending_item(failed.url, retry_of_run_id=parent.id)
    run = await start_naver_run(
        db,
        NaverRunStart(
            request.hospital.id,
            naver_blog_id_from(failed.url),
            (pending,),
            "NAVER_SOURCE_RETRY",
            parent.id,
        ),
    )
    outcome = await process_naver_item(
        NaverSourceProcess(
            db,
            request.hospital,
            run.id,
            None,
            request.actor[:100],
            request.actor,
        ),
        pending,
        fetch_url_text,
    )
    if _is_evidence_available(outcome):
        await record_naver_recovery(
            db,
            NaverIncidentContext(
                request.hospital.id,
                request.hospital.name,
                run.id,
                outcome,
                request.actor,
            ),
            retrying,
        )
    elif outcome.safe_error_code == "EMPTY_CONTENT":
        await record_naver_failure(
            db,
            NaverIncidentContext(
                request.hospital.id,
                request.hospital.name,
                parent.id,
                failed,
                request.actor,
            ),
        )
    await finish_naver_run(db, run, (outcome,))
    return _result(naver_blog_id_from(failed.url), run, (outcome,))


def _unique_post_urls(post_urls: list[str]) -> tuple[str, ...]:
    unique: dict[str, str] = {}
    for url in post_urls:
        unique.setdefault(naver_blog_post_hash(url), naver_blog_post_identity(url))
    return tuple(unique.values())


def _is_evidence_available(item: NaverHandoffItem) -> bool:
    return item.state == NaverHandoffState.INGESTED or (
        item.state == NaverHandoffState.SKIPPED
        and item.safe_error_code == "DUPLICATE_SOURCE"
    )


def _result(
    blog_id: str | None,
    run: OperationRun,
    items: tuple[NaverHandoffItem, ...],
) -> NaverHandoffResult:
    return NaverHandoffResult(
        blog_id=blog_id,
        requested=len(items),
        created=sum(item.state == NaverHandoffState.INGESTED for item in items),
        skipped_duplicate=sum(item.safe_error_code == "DUPLICATE_SOURCE" for item in items),
        skipped_empty=sum(item.safe_error_code == "EMPTY_CONTENT" for item in items),
        failed=tuple(
            item.safe_error_message or "네이버 글 수집 실패"
            for item in items
            if item.state == NaverHandoffState.FAILED
        ),
        items=items,
        source_ids=tuple(item.source_id for item in items if item.source_id is not None),
        run_id=run.id,
        parent_run_id=run.parent_run_id,
    )
