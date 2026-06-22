"""One-off: 대표 이미지 backfill / 재생성 for published ContentItems.

기본(backfill): image_url IS NULL 인 발행 콘텐츠에만 대표 이미지를 채운다(멱등).
강제(regen):   IMAGE_REGEN_FORCE=1 이면 이미지 유무와 무관하게 재생성
               (gpt-image-2 도입 시 기존 Imagen "파란 빈 방" 슬롭 이미지를 교체).
대상 상태:     IMAGE_REGEN_STATUS=published(기본)|draft|all 로 선택.

야간 생성 태스크와 동일한 image_engine.generate_image 를 사용하며, 각 항목의 제목을 topic으로
주입해 항목마다 다른 그림이 나오게 한다. 특정 병원만 대상으로 하려면 IMAGE_REGEN_SLUG 지정.

실행 (prod) — backend 이미지로 Cloud Run Job을 SERVICE=backfill-images 로 실행:
  docker-entrypoint.sh: backfill-images) -> python -m app.utils.backfill_content_images
  재생성: env IMAGE_REGEN_FORCE=1 (+ 선택 IMAGE_REGEN_SLUG=<slug>) override.
"""
import asyncio
import logging
import os

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.services.image_engine import generate_image

logger = logging.getLogger(__name__)

# 폭주 방지 — 한 번 실행에서 생성할 최대 이미지 수 (생성 비용 가드).
MAX_BACKFILL = 50


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    force = os.getenv("IMAGE_REGEN_FORCE", "").strip() in ("1", "true", "True")
    only_slug = os.getenv("IMAGE_REGEN_SLUG", "").strip() or None

    # 대상 상태: published(기본) | draft | all — DRAFT 콘텐츠도 발행 전에 gpt-image-2
    # 이미지로 정리해 두면 AE가 발행할 때 옛 Imagen 이미지가 라이브로 나가지 않는다.
    status_filter = os.getenv("IMAGE_REGEN_STATUS", "published").strip().lower()

    with SyncSessionLocal() as db:
        stmt = select(ContentItem)
        if status_filter == "published":
            stmt = stmt.where(ContentItem.status == ContentStatus.PUBLISHED)
        elif status_filter == "draft":
            stmt = stmt.where(ContentItem.status == ContentStatus.DRAFT)
        # "all" → 상태 필터 없음
        if not force:
            # backfill 모드: 이미지 없는 것만(멱등)
            stmt = stmt.where(ContentItem.image_url.is_(None))
        if only_slug:
            hospital = db.execute(
                select(Hospital).where(Hospital.slug == only_slug)
            ).scalar_one_or_none()
            if hospital is None:
                logger.error("Hospital not found: %s", only_slug)
                return
            stmt = stmt.where(ContentItem.hospital_id == hospital.id)
        stmt = stmt.order_by(ContentItem.published_at).limit(MAX_BACKFILL)

        items = db.execute(stmt).scalars().all()
        logger.info(
            "Image %s candidates: %d (force=%s, status=%s, slug=%s)",
            "regen" if force else "backfill", len(items), force, status_filter, only_slug or "-",
        )

        done = 0
        for item in items:
            hospital = db.get(Hospital, item.hospital_id)
            if hospital is None:
                logger.warning("SKIP %s — hospital missing", item.id)
                continue
            try:
                # generate_image는 async (내부에서 run_in_executor) — 동기 스크립트에서 asyncio.run.
                url, prompt = asyncio.run(
                    generate_image(item.content_type, hospital.slug, topic=item.title)
                )
            except Exception as e:  # noqa: BLE001 — 한 건 실패가 전체를 막지 않도록
                logger.error("FAIL %s (%s): %s", item.id, item.title, e)
                continue
            if not url:
                logger.warning(
                    "SKIP %s — generate_image returned empty (provider 미설정?)", item.id
                )
                continue
            item.image_url = url
            item.image_prompt = prompt
            db.commit()  # 건별 커밋 — 부분 성공을 보존.
            done += 1
            logger.info("OK [%d/%d] %s — %s", done, len(items), hospital.slug, item.title)

        logger.info("Image job complete: %d/%d generated", done, len(items))


if __name__ == "__main__":
    main()
