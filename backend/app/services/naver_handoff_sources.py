"""Create one evidence source from one durable Naver handoff item."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.essence import HospitalSourceAsset, SourceStatus, SourceType
from app.services.asset_extractor import (
    FetchQuality,
    naver_blog_id_from,
    naver_blog_post_identity,
)
from app.services.essence_engine import compute_source_content_hash
from app.services.naver_handoff_contracts import (
    NaverHandoffItem,
    failed_item,
    ingested_item,
    skipped_item,
)
from app.services.naver_handoff_incidents import NaverIncidentContext, record_naver_failure
from app.utils.db_locks import acquire_hospital_advisory_lock


class NaverHospitalRef(Protocol):
    id: uuid.UUID
    name: str
    blog_url: str | None


FetchText = Callable[[str], Awaitable[tuple[str, str | None, FetchQuality | None]]]


@dataclass(frozen=True, slots=True)
class NaverSourceProcess:
    db: AsyncSession
    hospital: NaverHospitalRef
    run_id: uuid.UUID
    operator_note: str | None
    created_by: str
    actor: str


async def process_naver_item(
    context: NaverSourceProcess,
    item: NaverHandoffItem,
    fetch_text: FetchText,
) -> NaverHandoffItem:
    existing_urls, existing_hashes = await _existing_source_keys(context.db, context.hospital.id)
    if item.url in existing_urls:
        return _duplicate(item)
    text, error, quality = await fetch_text(item.url)
    if error:
        failed = failed_item(item, error)
        await record_naver_failure(
            context.db,
            NaverIncidentContext(
                context.hospital.id,
                context.hospital.name,
                context.run_id,
                failed,
                context.actor,
            ),
        )
        return failed
    if not text.strip() or (quality is not None and quality.looks_like_shell):
        return skipped_item(
            item,
            "EMPTY_CONTENT",
            "본문이 비어 있거나 확인할 수 없어 근거 자료로 저장하지 않았습니다.",
        )

    blog_id = naver_blog_id_from(item.url) or "NAVER"
    title = f"네이버 블로그 {blog_id} {item.url.rsplit('/', 1)[-1]}"
    content_hash = compute_source_content_hash(title, item.url, text, context.operator_note)
    await acquire_hospital_advisory_lock(context.db, context.hospital.id)
    existing_urls, existing_hashes = await _existing_source_keys(context.db, context.hospital.id)
    if item.url in existing_urls or content_hash in existing_hashes:
        return _duplicate(item)
    source_id = uuid.uuid4()
    context.db.add(
        HospitalSourceAsset(
            id=source_id,
            hospital_id=context.hospital.id,
            source_type=SourceType.NAVER_BLOG,
            title=title,
            url=item.url,
            raw_text=text,
            operator_note=context.operator_note,
            source_metadata={
                "crawled_at": datetime.now(timezone.utc).isoformat(),
                "bulk_blog_id": blog_id,
                "auto_synced": context.created_by == "NAVER_WEEKLY_SYNC",
                "review_required": True,
            },
            content_hash=content_hash,
            status=SourceStatus.PENDING,
            created_by=context.created_by,
        )
    )
    await context.db.flush()
    return ingested_item(item, source_id)


async def _existing_source_keys(
    db: AsyncSession, hospital_id: uuid.UUID
) -> tuple[set[str], set[str]]:
    rows = (
        await db.execute(
            select(HospitalSourceAsset.url, HospitalSourceAsset.content_hash).where(
                HospitalSourceAsset.hospital_id == hospital_id
            )
        )
    ).all()
    urls = {naver_blog_post_identity(url) for url, _hash in rows if url}
    hashes = {content_hash for _url, content_hash in rows if content_hash}
    return urls, hashes


def _duplicate(item: NaverHandoffItem) -> NaverHandoffItem:
    return skipped_item(item, "DUPLICATE_SOURCE", "이미 수집된 글이라 다시 추가하지 않았습니다.")
