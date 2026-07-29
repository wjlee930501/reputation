"""네이버 블로그 신규 글 자동 인입.

신규 글은 원문 보존 자산(PENDING)으로만 추가한다. 병원 고유 주장과 진료 방침을
자동 승인하면 의료 콘텐츠 안전성이 깨질 수 있으므로, 근거 추출·운영 기준 반영은
Admin 검토 단계에서 진행한다.

주간 배치(weekly_naver_source_sync)로 실행된다. Celery `include`/`task_routes`/
`beat_schedule` 등록이 없으면 이 모듈은 한 번도 실행되지 않는 죽은 경로가 되므로,
태스크 정의는 반드시 celery_app.py 등록과 함께 유지한다.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.essence import HospitalSourceAsset, SourceStatus, SourceType
from app.models.hospital import Hospital, HospitalStatus
from app.services import notifier
from app.services.asset_extractor import (
    fetch_naver_blog_post_urls,
    fetch_url_text,
    naver_blog_id_from,
    naver_blog_post_identity,
)
from app.services.essence_engine import compute_source_content_hash

logger = logging.getLogger(__name__)

# 주간 배치 대상 병원 상태 — 운영 중(ACTIVE)뿐 아니라 도메인 연결 대기(PENDING_DOMAIN)도
# 포함한다. 근거 자료 축적은 공개 전부터 필요하고, 이때 모은 자료가 운영 기준 승인(STEP5)의
# 입력이 된다.
SYNC_TARGET_STATUSES = (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN)


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


_tls = threading.local()


def _run_async(coro):
    """동기 Celery 태스크에서 코루틴을 실행한다(tasks.py와 같은 규약).

    스레드당 이벤트 루프를 재사용한다 — 루프에 바인딩된 async 클라이언트가
    매 호출마다 루프가 바뀌면 커넥션 풀이 깨진다.
    """
    loop = getattr(_tls, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _tls.loop = loop
    return loop.run_until_complete(coro)


def _admin_essence_url(hospital_id: object) -> str:
    return f"{settings.ADMIN_BASE_URL.rstrip('/')}/hospitals/{hospital_id}/essence"


@celery_app.task(
    name="app.workers.naver_sync.weekly_naver_source_sync",
    soft_time_limit=1800,
    time_limit=2100,
)
def weekly_naver_source_sync():
    """매주 화요일 03:00 — 병원 네이버 블로그 신규 글을 검토 대기 자산으로 인입."""
    processed = 0
    created_total = 0
    failures: list[str] = []

    with SyncSessionLocal() as db:
        hospitals = db.execute(
            select(Hospital).where(
                Hospital.status.in_(SYNC_TARGET_STATUSES),
                Hospital.blog_url.is_not(None),
                Hospital.blog_url != "",
            )
        ).scalars().all()

        for hospital in hospitals:
            # 병원 단위 격리 — 한 병원의 크롤링 실패(네이버 차단·타임아웃)가 나머지
            # 병원의 인입을 막지 않게 한다. 주간 1회뿐이라 놓치면 일주일이 비어버린다.
            try:
                result = _run_async(sync_hospital_naver_sources(db, hospital))
            except Exception:
                logger.exception("naver weekly sync failed for %s; skipping", hospital.name)
                failures.append(hospital.name)
                db.rollback()
                continue

            processed += 1
            if result.error:
                logger.info("naver weekly sync skipped %s: %s", hospital.name, result.error)
                continue
            created_total += result.created
            if result.created:
                _run_async(
                    notifier.notify_naver_assets_synced(
                        hospital_name=hospital.name,
                        created=result.created,
                        requested=result.requested,
                        admin_url=_admin_essence_url(hospital.id),
                    )
                )

    logger.info(
        "weekly_naver_source_sync done: %s hospitals processed, %s assets created, %s failed",
        processed,
        created_total,
        len(failures),
    )

    if failures:
        names = ", ".join(failures[:10]) + (" 외" if len(failures) > 10 else "")
        _run_async(
            notifier.notify_ops_alert(
                title="네이버 자산 주간 인입 실패",
                message=(
                    f"{len(failures)}개 병원의 네이버 블로그 인입에 실패했습니다: {names}\n"
                    f"나머지 병원은 정상 처리됐습니다. 블로그 주소와 접근 차단 여부를 확인해 주세요."
                ),
            )
        )
    return {"processed": processed, "created": created_total, "failed": len(failures)}
