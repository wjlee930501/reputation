"""IndexNow 제출 — 발행한 콘텐츠를 검색 색인에 능동적으로 알린다.

왜 필요한가. 2026-07-29 프로덕션 측정에서 나온 사실:

  AI 답변이 병원 허브를 **인용한** 경우   14/15  = 93.3% 병원 언급
  AI 답변이 병원 허브를 인용하지 **않은** 경우  6/145 =  4.1% 병원 언급

읽히면 언급되고, 안 읽히면 거의 언급되지 않는다. 그런데 같은 병원의 대장내시경 주제
콘텐츠 7편은 질의와 제목이 거의 일대일로 대응하는데도 85회 측정에서 인용이 0회였다.
sitemap·robots는 정상이므로 배관 문제가 아니라 **색인에 들어갔는지**가 의심된다.

sitemap은 크롤러가 올 때까지 기다리는 수동적 신호다. IndexNow는 발행 즉시 밀어 넣는
능동적 신호이고, Bing 인덱스는 OpenAI 웹검색이 참조하는 인덱스다. 계정·API 키 발급
절차 없이 도메인에 키 파일만 두면 동작하므로 병원 도메인마다 수작업이 필요 없다.

키 파일은 **제출하는 URL과 같은 호스트**에서 서빙되어야 한다. `site/app/indexnow-key.txt`
라우트가 커스텀 도메인·플랫폼 호스트 양쪽에서 같은 키를 응답한다.

발행을 막지 않는다 — 실패는 로그와 반환값으로만 알린다.
"""
from __future__ import annotations

import logging
from time import monotonic
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.site_revalidate import content_site_paths, hospital_site_paths

logger = logging.getLogger(__name__)

# api.indexnow.org는 참여 검색엔진(Bing·Yandex·Seznam 등)에 함께 전달한다.
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
KEY_PATH = "/indexnow-key.txt"
# IndexNow 규격 상한은 10,000이나, 한 번에 크게 보내면 실패 시 전량이 날아간다.
MAX_URLS_PER_REQUEST = 500


def is_configured() -> bool:
    return bool(settings.INDEXNOW_ENABLED and settings.INDEXNOW_KEY)


def public_base_url(aeo_domain: str | None) -> str:
    """병원 공개 표면의 절대 base URL.

    자기 도메인을 연결했으면 그 도메인이 정본이다. 플랫폼 호스트로 제출하면
    커스텀 도메인 URL은 색인 신호를 못 받는다.
    """
    domain = (aeo_domain or "").strip().strip("/")
    if domain:
        if "://" not in domain:
            domain = f"https://{domain}"
        return domain.rstrip("/")
    return settings.SITE_BASE_URL.rstrip("/")


def _host_of(base_url: str) -> str:
    return (urlparse(base_url).hostname or "").lower()


def _absolute(base_url: str, paths: list[str]) -> list[str]:
    """상대 경로를 절대 URL로. revalidate 전용 경로는 색인 대상이 아니므로 제외한다."""
    skip = {"/sitemap.xml"}
    urls: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path in skip:
            continue
        url = f"{base_url}{path}" if path != "/" else f"{base_url}/"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


# 호스트별 소유 증명 확인 결과 캐시: host -> (verified, 만료 monotonic 시각)
_OWNERSHIP_CACHE: dict[str, tuple[bool, float]] = {}
_OWNERSHIP_TTL_SECONDS = 600.0
_OWNERSHIP_NEGATIVE_TTL_SECONDS = 60.0


async def _ownership_verified(base_url: str, host: str) -> bool:
    """제출 전에 `{base_url}/indexnow-key.txt`가 **설정과 같은 키**를 주는지 확인한다.

    backend와 site는 별도 서비스로 배포되므로 키가 한쪽에만 있는 구간이 생길 수 있다
    (site 먼저 배포 실패, 시크릿 생성 직후 backend만 배포 등). 그 상태로 제출하면
    IndexNow가 소유를 증명할 수 없는 요청을 받게 되고, 반복되면 도메인 신뢰도만 깎인다.
    배포 순서로 이 문제를 풀면 out-of-band 배포에서 다시 깨지므로 런타임에서 확인한다.
    """
    cached = _OWNERSHIP_CACHE.get(host)
    now = monotonic()
    if cached and cached[1] > now:
        return cached[0]

    verified = False
    try:
        # 리다이렉트를 따라가지 않는다. 키는 **제출하는 호스트가 직접** 응답해야 소유 증명이다.
        #  - 따라가면: 병원 도메인이 외부로 301하는 순간 남의 서버가 준 키로 "증명됨"이 되어
        #    소유 없는 호스트에 제출하게 된다(반복되면 도메인 신뢰도만 깎인다).
        #  - 따라가면: 목적지가 호스트명 allowlist(is_valid_hostname)를 우회하므로,
        #    병원이 자기 서버에서 내부 주소로 302하면 워커가 대신 GET하는 SSRF가 된다.
        # 단계별 타임아웃도 조인다 — 발행 루프(08:00) 안에서 호스트마다 1회씩 도는 경로다.
        timeout = httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=1.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            res = await client.get(f"{base_url}{KEY_PATH}")
        verified = (
            res.status_code == 200
            and (res.url.host or "").lower() == host
            and res.text.strip() == (settings.INDEXNOW_KEY or "").strip()
        )
        if not verified:
            logger.warning(
                "IndexNow 소유 증명 실패 — 제출 건너뜀 (host=%s status=%s). "
                "site 서비스에 backend와 동일한 INDEXNOW_KEY가 배포됐는지 확인 필요.",
                host,
                res.status_code,
            )
    except Exception as exc:  # noqa: BLE001 — 확인 실패는 제출을 막을 뿐 발행은 막지 않는다
        logger.warning("IndexNow 소유 증명 확인 불가 (host=%s): %s", host, exc)

    ttl = _OWNERSHIP_TTL_SECONDS if verified else _OWNERSHIP_NEGATIVE_TTL_SECONDS
    _OWNERSHIP_CACHE[host] = (verified, now + ttl)
    return verified


async def submit_urls(*, base_url: str, urls: list[str]) -> bool:
    """IndexNow에 URL 목록을 제출한다. 설정이 없으면 조용히 건너뛴다."""
    if not is_configured():
        logger.debug("IndexNow not configured; skipping submission")
        return False
    if not urls:
        return False

    host = _host_of(base_url)
    if not host:
        logger.warning("IndexNow: base_url에서 host를 얻지 못함 — %s", base_url)
        return False

    if not await _ownership_verified(base_url, host):
        return False

    # 다른 호스트의 URL이 섞이면 IndexNow가 전체 요청을 422로 거부한다.
    same_host = [u for u in urls if _host_of(u) == host]
    if len(same_host) != len(urls):
        logger.warning(
            "IndexNow: host 불일치 URL %d건 제외 (host=%s)", len(urls) - len(same_host), host
        )
    if not same_host:
        return False

    ok = True
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for start in range(0, len(same_host), MAX_URLS_PER_REQUEST):
            chunk = same_host[start : start + MAX_URLS_PER_REQUEST]
            payload = {
                "host": host,
                "key": settings.INDEXNOW_KEY,
                "keyLocation": f"{base_url}{KEY_PATH}",
                "urlList": chunk,
            }
            try:
                res = await client.post(INDEXNOW_ENDPOINT, json=payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("IndexNow 제출 실패 (host=%s): %s", host, exc)
                ok = False
                continue
            # 200 OK / 202 Accepted 모두 정상. 그 외는 본문에 사유가 담긴다.
            if res.status_code in (200, 202):
                logger.info("IndexNow 제출 완료: host=%s urls=%d", host, len(chunk))
            else:
                logger.warning(
                    "IndexNow 거부: host=%s status=%s body=%s",
                    host,
                    res.status_code,
                    (res.text or "")[:200],
                )
                ok = False
    return ok


async def submit_content_published(
    *,
    slug: str,
    content_id: object,
    aeo_domain: str | None,
    treatments: list | None = None,
) -> bool:
    """콘텐츠 발행 직후 호출 — 새 글과 그 글이 노출되는 목록/허브 페이지를 함께 알린다."""
    base = public_base_url(aeo_domain)
    return await submit_urls(
        base_url=base, urls=_absolute(base, content_site_paths(slug, content_id, treatments))
    )


async def submit_hospital_pages(
    *, slug: str, aeo_domain: str | None, treatments: list | None = None
) -> bool:
    base = public_base_url(aeo_domain)
    return await submit_urls(
        base_url=base, urls=_absolute(base, hospital_site_paths(slug, treatments))
    )


def hospital_all_urls(
    *,
    slug: str,
    aeo_domain: str | None,
    treatments: list | None = None,
    content_ids: list | None = None,
) -> tuple[str, list[str]]:
    """병원 공개 표면 전체(허브 페이지 + 발행 콘텐츠)의 절대 URL 목록.

    백필처럼 "이 병원의 색인 대상 전부"가 필요한 곳에서 쓴다.
    (base_url, urls)를 함께 돌려주는 이유는 submit_urls가 host 검증에 base_url을 쓰기 때문이다.
    """
    base = public_base_url(aeo_domain)
    paths = hospital_site_paths(slug, treatments)
    for content_id in content_ids or []:
        paths.append(f"/{slug}/contents/{content_id}")
    return base, _absolute(base, paths)


async def submit_content_published_safe(
    *,
    slug: str,
    content_id: object,
    aeo_domain: str | None,
    treatments: list | None = None,
) -> bool:
    """예외를 삼킨다 — 색인 신호 실패가 발행 파이프라인을 멈추게 두지 않는다."""
    try:
        return await submit_content_published(
            slug=slug, content_id=content_id, aeo_domain=aeo_domain, treatments=treatments
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("IndexNow 제출 중 예외 (slug=%s, content=%s): %s", slug, content_id, exc)
        return False
