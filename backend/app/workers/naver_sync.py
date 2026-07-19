"""네이버 블로그 신규 글 자동 인입.

신규 글은 원문 보존 자산(PENDING)으로만 추가한다. 병원 고유 주장과 진료 방침을
자동 승인하면 의료 콘텐츠 안전성이 깨질 수 있으므로, 근거 추출·운영 기준 반영은
Admin 검토 단계에서 진행한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.essence import HospitalSourceAsset, SourceStatus, SourceType
from app.services.asset_extractor import (
    fetch_naver_blog_post_urls,
    fetch_url_text,
    naver_blog_id_from,
    naver_blog_post_identity,
)
from app.services.essence_engine import compute_source_content_hash


@dataclass
class NaverSyncResult:
    blog_id: str | None
    requested: int = 0
    created: int = 0
    skipped_duplicate: int = 0
    skipped_empty: int = 0
    failed: list[str] = field(default_factory=list)
    error: str | None = None


async def sync_hospital_naver_sources(db, hospital, *, max_posts: int = 15) -> NaverSyncResult:
    """한 병원의 RSS 최근 글을 중복 없이 source asset으로 추가하고 커밋한다."""
    blog_ref = (getattr(hospital, "blog_url", None) or "").strip()
    blog_id = naver_blog_id_from(blog_ref)
    result = NaverSyncResult(blog_id=blog_id)
    if not blog_id:
        result.error = "네이버 블로그 주소를 인식하지 못했습니다."
        return result

    post_urls, enum_error = await fetch_naver_blog_post_urls(blog_ref, max_posts=max_posts)
    if enum_error:
        result.error = enum_error
        return result
    result.requested = len(post_urls)

    existing = db.execute(
        select(HospitalSourceAsset.url, HospitalSourceAsset.content_hash).where(
            HospitalSourceAsset.hospital_id == hospital.id
        )
    ).all()
    existing_urls = {naver_blog_post_identity(url) for url, _hash in existing if url}
    existing_hashes = {_hash for _url, _hash in existing if _hash}

    for post_url in post_urls:
        post_identity = naver_blog_post_identity(post_url)
        if post_identity in existing_urls:
            result.skipped_duplicate += 1
            continue
        text, error, quality = await fetch_url_text(post_url)
        if error:
            result.failed.append(f"{post_url}: {error}")
            continue
        if not text or not text.strip() or (quality is not None and quality.looks_like_shell):
            result.skipped_empty += 1
            continue

        log_no = post_url.split("/", 4)[-1].split("?", 1)[0]
        title = f"네이버 블로그 {blog_id} {log_no}"
        content_hash = compute_source_content_hash(title, post_url, text)
        if content_hash in existing_hashes:
            result.skipped_duplicate += 1
            continue

        db.add(
            HospitalSourceAsset(
                hospital_id=hospital.id,
                source_type=SourceType.NAVER_BLOG,
                title=title,
                url=post_url,
                raw_text=text,
                source_metadata={
                    "crawled_at": datetime.now(timezone.utc).isoformat(),
                    "bulk_blog_id": blog_id,
                    "auto_synced": True,
                    "review_required": True,
                },
                content_hash=content_hash,
                status=SourceStatus.PENDING,
                created_by="NAVER_WEEKLY_SYNC",
            )
        )
        existing_urls.add(post_identity)
        existing_hashes.add(content_hash)
        result.created += 1

    if result.created:
        db.commit()
    return result
