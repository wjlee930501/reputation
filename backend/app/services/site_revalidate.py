"""Trigger Next.js site revalidation after publish.

콘텐츠 발행 직후 sitemap, hub, 콘텐츠 목록 페이지 캐시를 무효화해 OAI-SearchBot /
GPTBot / Googlebot 이 새 콘텐츠를 빨리 발견하도록 한다.

설정: SITE_REVALIDATE_URL + SITE_REVALIDATE_SECRET. 둘 중 하나라도 비어 있으면
호출을 생략한다 (개발 환경 또는 미설치 인스턴스에서 silently no-op).
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def trigger_site_revalidate(*, paths: list[str]) -> bool:
    if not settings.SITE_REVALIDATE_URL or not settings.SITE_REVALIDATE_SECRET:
        return False
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                settings.SITE_REVALIDATE_URL,
                json={"paths": paths},
                headers={"x-revalidate-secret": settings.SITE_REVALIDATE_SECRET},
            )
            response.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("site revalidate failed for paths=%s: %s", paths, exc)
        return False
