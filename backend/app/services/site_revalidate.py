"""Trigger Next.js site revalidation after public-surface mutations."""
import logging

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


def ensure_site_revalidate_configured() -> None:
    """Fail closed in production when public pages would otherwise stay stale."""
    if settings.SITE_REVALIDATE_URL and settings.SITE_REVALIDATE_SECRET:
        return
    if settings.APP_ENV.lower() == "production":
        raise HTTPException(
            status_code=503,
            detail="SITE_REVALIDATE_URL and SITE_REVALIDATE_SECRET must be configured in production.",
        )


def hospital_site_paths(slug: str) -> list[str]:
    base = f"/{slug}"
    return [
        "/sitemap.xml",
        base,
        f"{base}/contents",
        f"{base}/doctor",
        f"{base}/treatments",
        f"{base}/visit",
        f"{base}/llms.txt",
    ]


def content_site_paths(slug: str, content_id: object) -> list[str]:
    return [
        *hospital_site_paths(slug),
        f"/{slug}/contents/{content_id}",
    ]


async def trigger_hospital_site_revalidate(slug: str) -> bool:
    return await trigger_site_revalidate(paths=hospital_site_paths(slug))


async def trigger_content_site_revalidate(slug: str, content_id: object) -> bool:
    return await trigger_site_revalidate(paths=content_site_paths(slug, content_id))


async def trigger_site_revalidate(*, paths: list[str]) -> bool:
    if not settings.SITE_REVALIDATE_URL or not settings.SITE_REVALIDATE_SECRET:
        if settings.APP_ENV.lower() == "production":
            raise RuntimeError("SITE_REVALIDATE_URL and SITE_REVALIDATE_SECRET are required in production")
        return False
    clean_paths = _normalize_paths(paths)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                settings.SITE_REVALIDATE_URL,
                json={"paths": clean_paths},
                headers={"x-revalidate-secret": settings.SITE_REVALIDATE_SECRET},
            )
            response.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("site revalidate failed for paths=%s: %s", clean_paths, exc)
        if settings.APP_ENV.lower() == "production":
            raise
        return False


def _normalize_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        if not path or not path.startswith("/"):
            continue
        if path not in unique:
            unique.append(path)
    return unique
