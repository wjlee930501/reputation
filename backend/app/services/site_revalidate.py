"""Trigger Next.js site revalidation after public-surface mutations."""

import logging
import re
import unicodedata
import uuid
from datetime import datetime
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services.hospital_revalidation_control import start_hospital_revalidation_failure
from app.services.site_revalidation_control import (
    REVALIDATION_RETRY_DELAYS_SECONDS as _REVALIDATION_RETRY_DELAYS_SECONDS,
)
from app.services.site_revalidation_control import (
    start_revalidation_failure,
)
from app.workers.dispatch_auth import build_dispatch_headers

logger = logging.getLogger(__name__)
REVALIDATION_RETRY_DELAYS_SECONDS = _REVALIDATION_RETRY_DELAYS_SECONDS

# site/lib/treatment-slug.ts buildTreatmentSlug와 동일한 규칙 — pillar URL이 양쪽에서
# 어긋나면 revalidate가 잘못된 경로를 두드린다.
_FORBIDDEN_URL_CHARS = re.compile(r"[\s/?#&=%+]+")


def ensure_site_revalidate_configured() -> None:
    """Fail closed in production when public pages would otherwise stay stale."""
    if settings.SITE_REVALIDATE_URL and settings.SITE_REVALIDATE_SECRET:
        return
    if settings.APP_ENV.lower() == "production":
        raise HTTPException(
            status_code=503,
            detail="SITE_REVALIDATE_URL and SITE_REVALIDATE_SECRET must be configured in production.",
        )


def build_treatment_slug(name: str | None) -> str:
    """site/lib/treatment-slug.ts buildTreatmentSlug의 Python 포트."""
    if not name:
        return ""
    slug = unicodedata.normalize("NFKC", name).strip()
    slug = _FORBIDDEN_URL_CHARS.sub("-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug.lower()


def _treatment_pillar_paths(slug: str, treatments: list | None) -> list[str]:
    """treatment pillar 풀페이지 경로 — Next.js dynamic segment는 percent-encoded
    형태로도 캐시 키가 잡힐 수 있어 decoded/encoded 두 형태를 모두 보낸다."""
    paths: list[str] = []
    for treatment in treatments or []:
        name = treatment.get("name") if isinstance(treatment, dict) else None
        treatment_slug = build_treatment_slug(name)
        if not treatment_slug:
            continue
        paths.append(f"/{slug}/treatments/{treatment_slug}")
        encoded = quote(treatment_slug, safe="-")
        if encoded != treatment_slug:
            paths.append(f"/{slug}/treatments/{encoded}")
    return paths


def hospital_site_paths(slug: str, treatments: list | None = None) -> list[str]:
    base = f"/{slug}"
    return [
        # Custom-domain requests enter at `/` and middleware rewrites them to
        # `/{slug}`. Next can retain the host-root route cache separately, so
        # invalidating only the rewritten pathname leaves e.g. jangclinic.kr
        # showing an old profile/image until ISR expiry.
        "/",
        "/sitemap.xml",
        "/llms.txt",  # 루트 llms.txt도 병원 목록/요약을 노출하므로 함께 무효화 (P2-9b)
        base,
        f"{base}/contents",
        f"{base}/doctor",
        f"{base}/treatments",
        *_treatment_pillar_paths(slug, treatments),
        f"{base}/visit",
        f"{base}/llms.txt",
    ]


def content_site_paths(slug: str, content_id: object, treatments: list | None = None) -> list[str]:
    return [
        *hospital_site_paths(slug, treatments),
        f"/{slug}/contents/{content_id}",
    ]


async def trigger_hospital_site_revalidate(slug: str, treatments: list | None = None) -> bool:
    return await trigger_site_revalidate(paths=hospital_site_paths(slug, treatments))


async def trigger_hospital_site_revalidate_safe(
    slug: str,
    treatments: list | None = None,
    *,
    hospital_name: str | None = None,
) -> bool:
    """커밋 이후 호출용 — 실패해도 절대 raise하지 않는다 (R4, content _safe와 동일 패턴).

    프로파일/도메인/활성화/자료 공개 토글은 이미 커밋된 뒤이므로, revalidate 실패로
    500을 돌려주면 저장이 실패한 것처럼 보인다. 경고 로그 + Slack 운영 알림으로 강등.
    """
    try:
        return await trigger_site_revalidate(paths=hospital_site_paths(slug, treatments))
    except Exception as exc:
        logger.warning(
            "post-commit hospital site revalidate failed for %s: %s",
            slug,
            exc.__class__.__name__,
        )
        try:
            plan = await start_hospital_revalidation_failure(slug)
            if plan is not None and plan.created and plan.delay_seconds is not None:
                from app.core.celery_app import celery_app

                celery_app.send_task(
                    "app.workers.tasks.retry_site_revalidation",
                    args=[str(plan.run_id), 0],
                    queue="default",
                    countdown=plan.delay_seconds,
                    headers=build_dispatch_headers(
                        "retry-site-revalidation", str(plan.run_id)
                    ),
                )
        except Exception:
            logger.exception("durable hospital revalidation recovery setup failed")
        return False


async def trigger_content_site_revalidate_safe(
    slug: str,
    content_id: object,
    *,
    hospital_name: str | None = None,
    treatments: list | None = None,
    unpublished_from: datetime | None = None,
) -> bool:
    """커밋 이후 호출용 — 실패해도 절대 raise하지 않는다 (P2-9b).

    발행 커밋 뒤 revalidate 실패로 500을 돌려주면 AE는 실패로 인지하고 재시도하다
    "Already published"를 만난다. 프로덕션 포함, 경고 로그 + Slack 운영 알림으로 강등.

    반려·비공개(내림)로 호출할 때는 `unpublished_from`에 직전 published_at을 넘긴다.
    반려가 발행 메타를 지우기 때문에, 이 값이 없으면 내려간 글이 어느 판(edition)으로
    캐시에 남아 있는지 식별할 수 없어 내구성 있는 재시도가 열리지 않는다.
    무효화 경로 자체는 올림과 동일한 content_site_paths 전체다.
    """
    try:
        return await trigger_site_revalidate(paths=content_site_paths(slug, content_id, treatments))
    except Exception as exc:
        logger.warning("post-commit site revalidate failed: code=%s", exc.__class__.__name__)
        try:
            parsed_content_id = uuid.UUID(str(content_id))
        except (TypeError, ValueError):
            logger.warning("revalidation failure has invalid content identity")
            return False
        try:
            plan = await start_revalidation_failure(
                slug, parsed_content_id, unpublished_from=unpublished_from
            )
            if plan is None:
                logger.warning(
                    "durable revalidation recovery skipped: code=tenant_or_publication_not_found"
                )
            elif plan.created and plan.delay_seconds is not None:
                from app.core.celery_app import celery_app

                celery_app.send_task(
                    "app.workers.tasks.retry_site_revalidation",
                    args=[str(plan.run_id), 0],
                    queue="default",
                    countdown=plan.delay_seconds,
                    headers=build_dispatch_headers(
                        "retry-site-revalidation", str(plan.run_id)
                    ),
                )
        except Exception:
            logger.exception("durable revalidation recovery setup failed (non-fatal)")
        return False


async def trigger_site_revalidate(*, paths: list[str]) -> bool:
    if not settings.SITE_REVALIDATE_URL or not settings.SITE_REVALIDATE_SECRET:
        if settings.APP_ENV.lower() == "production":
            raise RuntimeError(
                "SITE_REVALIDATE_URL and SITE_REVALIDATE_SECRET are required in production"
            )
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
