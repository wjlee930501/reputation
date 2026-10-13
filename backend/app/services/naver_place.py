"""네이버 플레이스 스크래핑 — Jina Reader 경유.

네이버 지도/플레이스는 봇 차단(ncaptcha)이 강해 데이터센터 IP(Cloud Run egress)에서
직접 호출하면 캡차/429로 막힌다. Jina Reader(r.jina.ai)가 서버측에서 페이지를 읽어
마크다운으로 돌려주므로 백엔드 egress IP 차단을 우회한다 — 새 의존성 없이 httpx만 사용.

흐름:
  1. 병원 행에 저장된 place id가 있으면 그대로 쓴다(신원 STORED_ID).
  2. 없으면 search_place_id(name): m.place 검색 결과 마크다운에서 첫 place_id 추출 —
     상세 본문의 상호·지역이 병원과 맞을 때만 VERIFIED_SEARCH, 아니면 UNVERIFIED(E4).
  3. fetch_place_markdown(place_id): 상세(home + information) 마크다운 수집

실패는 예외 대신 빈 결과 + 사유로 우아하게 처리한다(best-effort). 네이버가 막아도
홈페이지/블로그 소스는 계속 동작하고 AE가 수동 보완한다.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
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


#: 네이버 플레이스 신원 확인 결과(E4). 확인되지 않은 네이버 값은 초안에만 남고 자동 적용되지 않는다.
IDENTITY_STORED_ID = "STORED_ID"  # 병원 행에 저장된 place id를 그대로 썼다(검색 안 함)
IDENTITY_VERIFIED_SEARCH = "VERIFIED_SEARCH"  # 검색 결과의 상호·지역이 병원과 일치했다
IDENTITY_UNVERIFIED = "UNVERIFIED"  # 검색 첫 결과지만 상호·지역이 맞는지 확인하지 못했다
VERIFIED_IDENTITIES = frozenset({IDENTITY_STORED_ID, IDENTITY_VERIFIED_SEARCH})

# 저장된 플레이스 주소에서 id를 읽는다(m.place·map.naver·place.naver 공통). 단축 링크(naver.me)는
# id가 없어 검색으로 넘어간다.
_STORED_PLACE_ID_RE = re.compile(r"(?:place\.naver\.com|map\.naver\.com)/\S*?(\d{6,})")


@dataclass(frozen=True)
class NaverPlaceResult:
    place_id: str | None
    markdown: str
    reason: str | None  # 실패/부분 성공 사유 (AE 안내용)
    identity: str | None = None  # IDENTITY_* — place id를 못 찾았으면 None

    @property
    def verified(self) -> bool:
        return self.identity in VERIFIED_IDENTITIES

    @property
    def canonical_url(self) -> str | None:
        if not self.place_id or not self.place_id.isdigit():
            return None
        return f"https://{NAVER_PLACE_HOST}/hospital/{self.place_id}/home"


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


def stored_place_id(place_id: str | None, place_url: str | None) -> str | None:
    """병원 행에 저장된 네이버 플레이스 id. id 칸이 우선이고, 없으면 주소에서 읽는다."""

    cleaned = (place_id or "").strip()
    if cleaned.isdigit():
        return cleaned
    match = _STORED_PLACE_ID_RE.search(place_url or "")
    return match.group(1) if match else None


def _compact(value: str) -> str:
    return re.sub(r"[\s()（）·.,\-]+", "", value or "").lower()


def place_matches_hospital(markdown: str, name: str, regions: Sequence[str] = ()) -> bool:
    """검색으로 찾은 플레이스가 이 병원인지 — 상호가 정규화 일치하고 지역이 주소에 있는가.

    상호는 플레이스 본문에 병원명이 (공백·기호를 무시하고) 그대로 들어 있어야 한다. 지역 태그가
    있으면 그중 하나가 본문(주소)에 들어 있어야 한다. 지점명이 붙은 다른 병원("다온의원 세종점")이나
    동명 병원의 다른 지역 지점은 확인되지 않은 것으로 남긴다.
    """

    compact_markdown = _compact(markdown)
    compact_name = _compact(name)
    if not compact_name or compact_name not in compact_markdown:
        return False
    # 지역 태그는 낱말마다 본다 — "서울 성동구"는 "서울특별시 성동구" 주소와 맞아야 한다.
    region_words = [
        [_compact(word) for word in str(region).split() if _compact(word)] for region in regions
    ]
    region_words = [words for words in region_words if words]
    if not region_words:
        return True
    return any(all(word in compact_markdown for word in words) for words in region_words)


async def scrape_naver_place(
    name: str,
    *,
    place_id: str | None = None,
    regions: Sequence[str] = (),
) -> NaverPlaceResult:
    """네이버 플레이스를 스크랩하고 신원을 확인한다(E4).

    ``place_id``(병원 행에 저장된 id)가 있으면 검색하지 않고 그 id를 쓴다(STORED_ID). 없으면 병원명
    검색의 첫 결과를 읽고, 상호·지역이 병원과 맞을 때만 VERIFIED_SEARCH다. place_id를 못 찾거나
    본문이 비면 markdown="" + reason. best-effort.
    """
    stored = (place_id or "").strip()
    if stored.isdigit():
        markdown = await fetch_place_markdown(stored)
        if not markdown:
            return NaverPlaceResult(
                stored, "", "네이버 플레이스 상세 정보를 가져오지 못했습니다.", IDENTITY_STORED_ID
            )
        return NaverPlaceResult(stored, markdown, None, IDENTITY_STORED_ID)

    found = await search_place_id(name)
    if not found:
        return NaverPlaceResult(None, "", "네이버 플레이스에서 병원을 찾지 못했습니다.")
    markdown = await fetch_place_markdown(found)
    if not markdown:
        return NaverPlaceResult(
            found, "", "네이버 플레이스 상세 정보를 가져오지 못했습니다.", IDENTITY_UNVERIFIED
        )
    if place_matches_hospital(markdown, name, regions):
        return NaverPlaceResult(found, markdown, None, IDENTITY_VERIFIED_SEARCH)
    return NaverPlaceResult(
        found,
        markdown,
        "검색한 네이버 플레이스가 이 병원과 같은지 확인하지 못해 자동 적용하지 않습니다.",
        IDENTITY_UNVERIFIED,
    )
