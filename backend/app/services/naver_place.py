"""네이버 플레이스 스크래핑 — Jina Reader 경유.

네이버 지도/플레이스는 봇 차단(ncaptcha)이 강해 데이터센터 IP(Cloud Run egress)에서
직접 호출하면 캡차/429로 막힌다. Jina Reader(r.jina.ai)가 서버측에서 페이지를 읽어
마크다운으로 돌려주므로 백엔드 egress IP 차단을 우회한다 — 새 의존성 없이 httpx만 사용.

흐름:
  1. search_place_id(name): m.place 검색 결과 마크다운에서 첫 place_id 추출
  2. fetch_place_markdown(place_id): 상세(home + information) 마크다운 수집

실패는 예외 대신 빈 결과 + 사유로 우아하게 처리한다(best-effort). 네이버가 막아도
홈페이지/블로그 소스는 계속 동작하고 AE가 수동 보완한다.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

JINA_READER_BASE = "https://r.jina.ai/"
NAVER_PLACE_HOST = "m.place.naver.com"
_JINA_TIMEOUT = 30.0
_MAX_PLACE_TEXT = 60_000
# 검색 결과 마크다운에 박히는 상세 링크: m.place.naver.com/hospital/1595672233
_PLACE_ID_RE = re.compile(r"m\.place\.naver\.com/hospital/(\d{6,})")


@dataclass(frozen=True)
class NaverPlaceResult:
    place_id: str | None
    markdown: str
    reason: str | None  # 실패/부분 성공 사유 (AE 안내용)


async def fetch_via_jina(target_url: str) -> tuple[str, str | None]:
    """Jina Reader로 target_url을 읽어 (마크다운, 오류사유) 반환.

    백엔드가 대상 URL을 직접 치지 않고 Jina(r.jina.ai)가 서버측에서 읽으므로
    봇 차단(네이버 등)·peer 검증·SSRF 노출을 우회한다. best-effort.
    """
    headers = {
        "Accept": "text/plain, text/markdown, */*",
        "X-Return-Format": "markdown",
    }
    api_key = (settings.JINA_API_KEY or "").strip()
    if api_key:
        # 무인증 free tier는 분당 요청 제한이 빡빡하다. 키가 있으면 상향 적용.
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{JINA_READER_BASE}{target_url}"
    try:
        async with httpx.AsyncClient(timeout=_JINA_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text[:_MAX_PLACE_TEXT], None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 429:
            return "", "Jina Reader 호출이 일시적으로 제한되었습니다(429). 잠시 후 재시도."
        return "", f"네이버 플레이스 읽기 실패 (Jina HTTP {status})."
    except Exception as exc:  # noqa: BLE001 — best-effort, 어떤 실패도 폴백
        logger.warning("Jina fetch failed for %s: %s", target_url, exc)
        return "", f"네이버 플레이스 읽기 중 오류 — {exc}"


async def search_place_id(name: str) -> str | None:
    """병원명으로 네이버 플레이스 검색 → 첫 결과의 place_id."""
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    target = f"https://{NAVER_PLACE_HOST}/hospital/list?query={quote(cleaned)}"
    text, err = await fetch_via_jina(target)
    if err or not text:
        return None
    match = _PLACE_ID_RE.search(text)
    return match.group(1) if match else None


async def fetch_place_markdown(place_id: str) -> str:
    """상세(home + information) 페이지 마크다운을 합쳐 반환."""
    if not place_id or not place_id.isdigit():
        return ""
    home_url = f"https://{NAVER_PLACE_HOST}/hospital/{place_id}/home"
    info_url = f"https://{NAVER_PLACE_HOST}/hospital/{place_id}/information"
    home, info = await asyncio.gather(fetch_via_jina(home_url), fetch_via_jina(info_url))
    parts: list[str] = []
    if home[0]:
        parts.append("=== 네이버 플레이스 (홈) ===\n" + home[0])
    if info[0]:
        parts.append("=== 네이버 플레이스 (정보) ===\n" + info[0])
    return "\n\n".join(parts)[:_MAX_PLACE_TEXT]


async def scrape_naver_place(name: str) -> NaverPlaceResult:
    """병원명으로 네이버 플레이스를 스크랩.

    place_id를 못 찾거나 본문이 비면 markdown="" + reason. best-effort.
    """
    place_id = await search_place_id(name)
    if not place_id:
        return NaverPlaceResult(None, "", "네이버 플레이스에서 병원을 찾지 못했습니다.")
    markdown = await fetch_place_markdown(place_id)
    if not markdown:
        return NaverPlaceResult(place_id, "", "네이버 플레이스 상세 정보를 가져오지 못했습니다.")
    return NaverPlaceResult(place_id, markdown, None)
