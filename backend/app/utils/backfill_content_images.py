"""One-off: backfill 대표 이미지 for published ContentItems missing image_url.

이미지 생성 없이 seed된 콘텐츠(image_url IS NULL)에 Imagen 대표 이미지를 채운다.
야간 생성 태스크와 동일한 image_engine.generate_image 사용 — 동일 의료 일러스트 스타일.

실행 (prod) — backend 이미지로 Cloud Run Job을 SERVICE=backfill-images 로 실행:
  docker-entrypoint.sh: backfill-images) -> python -m app.utils.backfill_content_images

멱등: image_url이 NULL인 것만 채우므로 재실행해도 기존 이미지를 덮어쓰지 않는다.
"""
import asyncio
import logging

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.services.image_engine import generate_image

logger = logging.getLogger(__name__)

# 폭주 방지 — 한 번 실행에서 생성할 최대 이미지 수 (Imagen 비용 가드).
MAX_BACKFILL = 50


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SyncSessionLocal() as db:
        items = (
            db.execute(
                select(ContentItem)
                .where(
                    ContentItem.status == ContentStatus.PUBLISHED,
                    ContentItem.image_url.is_(None),
                )
                .order_by(ContentItem.published_at)
                .limit(MAX_BACKFILL)
            )
            .scalars()
            .all()
        )
        logger.info("Backfill candidates (published, no image): %d", len(items))

        done = 0
        for item in items:
            hospital = db.get(Hospital, item.hospital_id)
            if hospital is None:
                logger.warning("SKIP %s — hospital missing", item.id)
                continue
            try:
                # generate_image는 async (내부에서 run_in_executor) — 동기 스크립트에서 asyncio.run.
                url, prompt = asyncio.run(generate_image(item.content_type, hospital.slug))
            except Exception as e:  # noqa: BLE001 — 한 건 실패가 전체를 막지 않도록
                logger.error("FAIL %s (%s): %s", item.id, item.title, e)
                continue
            if not url:
                logger.warning(
                    "SKIP %s — generate_image returned empty (GCP_PROJECT_ID 미설정?)", item.id
                )
                continue
            item.image_url = url
            item.image_prompt = prompt
            db.commit()  # 건별 커밋 — 부분 성공을 보존.
            done += 1
            logger.info("OK [%d/%d] %s — %s", done, len(items), hospital.slug, item.title)

        logger.info("Backfill complete: %d/%d images generated", done, len(items))


if __name__ == "__main__":
    main()
