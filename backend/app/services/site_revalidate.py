"""Trigger Next.js site revalidation after public-surface mutations."""
import logging
import re
import unicodedata
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

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
    from app.services import notifier

    try:
        return await trigger_site_revalidate(paths=hospital_site_paths(slug, treatments))
    except Exception as exc:
        logger.warning("post-commit hospital site revalidate failed for %s: %s", slug, exc)
        try:
            await notifier.notify_ops_alert(
                title="공개 페이지 캐시 무효화 실패",
                message=(
                    f"병원: {hospital_name or slug}\n"
                    f"변경 사항은 정상 저장되었지만 공개 페이지 캐시 갱신에 실패했습니다.\n"
                    f"오류: `{str(exc)[:200]}`\n"
                    f"공개 페이지가 잠시 이전 상태로 보일 수 있습니다. 필요 시 수동 재검증해 주세요."
                ),
            )
        except Exception:
            logger.exception("revalidate failure ops alert delivery failed (non-fatal)")
        return False


async def trigger_content_site_revalidate_safe(
    slug: str,
    content_id: object,
    *,
    hospital_name: str | None = None,
    treatments: list | None = None,
) -> bool:
    """커밋 이후 호출용 — 실패해도 절대 raise하지 않는다 (P2-9b).

    발행 커밋 뒤 revalidate 실패로 500을 돌려주면 AE는 실패로 인지하고 재시도하다
    "Already published"를 만난다. 프로덕션 포함, 경고 로그 + Slack 운영 알림으로 강등.
    """
    from app.services import notifier

    try:
        return await trigger_site_revalidate(paths=content_site_paths(slug, content_id, treatments))
    except Exception as exc:
        logger.warning(
            "post-commit site revalidate failed for %s/%s: %s", slug, content_id, exc
        )
        try:
            await notifier.notify_ops_alert(
                title="공개 페이지 캐시 무효화 실패",
                message=(
                    f"병원: {hospital_name or slug}\n"
                    f"콘텐츠: `{content_id}`\n"
                    f"발행/반려는 정상 반영되었지만 공개 페이지 캐시 갱신에 실패했습니다.\n"
                    f"오류: `{str(exc)[:200]}`\n"
                    f"공개 페이지가 잠시 이전 상태로 보일 수 있습니다. 필요 시 수동 재검증해 주세요."
                ),
            )
        except Exception:
            logger.exception("revalidate failure ops alert delivery failed (non-fatal)")
        return False


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
