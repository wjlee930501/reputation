"""네이버 플레이스 스크래핑 — Jina Reader 경유.

네이버 지도/플레이스는 봇 차단(ncaptcha)이 강해 데이터센터 IP(Cloud Run egress)에서
직접 호출하면 캡차/429로 막힌다. Jina Reader(r.jina.ai)가 서버측에서 페이지를 읽어
마크다운으로 돌려주므로 백엔드 egress IP 차단을 우회한다 — 새 의존성 없이 httpx만 사용.

흐름:
  1. 병원 행에 저장된 place id가 있으면 그대로 쓴다(신원 STORED_ID).
  2. 없으면 search_place_id(name): m.place 검색 결과 마크다운에서 첫 place_id 추출 —
     상세 페이지에서 따로 읽은 상호가 병원명과 같고 주소가 병원 지역과 맞을 때만 VERIFIED_SEARCH,
     아니면(지역·주소를 확인할 수 없을 때 포함) UNVERIFIED(E4).
  3. fetch_place_markdown(place_id): 상세(home + information) 마크다운 수집

실패는 예외 대신 빈 결과 + 사유로 우아하게 처리한다(best-effort). 네이버가 막아도
홈페이지/블로그 소스는 계속 동작하고 AE가 수동 보완한다.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
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
    # NFC — 조합형(NFD) 한글로 온 같은 이름도 같게 본다.
    text = unicodedata.normalize("NFC", value or "")
    return re.sub(r"[\s()（）·.,\-]+", "", text).lower()


# Jina Reader가 앞에 붙이는 머리 줄과 fetch_place_markdown의 구획 표시 — 상호·주소 후보가 아니다.
_JINA_META_RE = re.compile(
    r"^(?:URL Source|Markdown Content|Published Time|Warning)\s*:", re.IGNORECASE
)
_SECTION_RE = re.compile(r"^=+.*=+$")
_SETEXT_RE = re.compile(r"^[=\-]{3,}$")
_TITLE_RE = re.compile(r"^Title\s*:\s*(.+)$", re.IGNORECASE)
# 페이지 제목 끝의 서비스 표기("장편한외과의원 : 네이버", "… - 네이버 지도", "… | 네이버 플레이스").
_TITLE_SUFFIX_RE = re.compile(r"\s*[:|\-–]\s*네이버(?:\s*(?:지도|플레이스|스마트플레이스))?\s*$")
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_ADDRESS_LABEL_RE = re.compile(r"^(?:도로명\s*)?주소\s*[:：]?\s*(.*)$")
_PROVINCES = frozenset(
    {
        "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원",
        "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    }
)
_ADMIN_SUFFIX_RE = re.compile(r"(?:특별자치시|특별자치도|특별시|광역시|도|시|군|구)$")
_ADDRESS_PART_RE = re.compile(r"^\S+(?:시|군|구|동|읍|면|로|길|리)$|^\d")
# 시·도 긴 이름 → 짧은 이름. "세종시"·"세종특별자치시"·"세종"은 같은 시·도다.
_PROVINCE_LONG_NAMES = {
    "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주",
    "강원특별자치도": "강원",
}
_PROVINCE_UNIT_RE = re.compile(r"(?:특별자치시|특별자치도|특별시|광역시|시|도)$")
# 시·군·구와 읍·면·동 — 주소 앞쪽의 행정 토큰. 도로명(…로·…길)·건물명은 행정 토큰이 아니다.
_LOCAL_UNIT_RE = re.compile(r"^\S+(?:시|군|구|읍|면|동)$")


def _plain_line(line: str) -> str:
    text = _MD_LINK_RE.sub(r"\1", line)
    text = re.sub(r"^\s*(?:#{1,6}\s+|[-*+>]\s+)", "", text)
    return text.replace("**", "").replace("__", "").strip()


def _content_lines(markdown: str) -> list[str]:
    lines: list[str] = []
    for raw in (markdown or "").splitlines():
        line = raw.strip()
        if not line or _SECTION_RE.match(line) or _SETEXT_RE.match(line):
            continue
        if _JINA_META_RE.match(line) or _TITLE_RE.match(line):
            continue
        plain = _plain_line(line)
        if plain:
            lines.append(plain)
    return lines


def _looks_like_address(line: str) -> bool:
    tokens = line.split()
    if len(tokens) < 2:
        return False
    first = tokens[0]
    if not (first in _PROVINCES or (len(first) >= 2 and _ADMIN_SUFFIX_RE.search(first))):
        return False
    return bool(_ADDRESS_PART_RE.match(tokens[1]))


def parse_place_identity(markdown: str) -> tuple[str | None, str | None]:
    """상세 페이지 마크다운에서 (상호, 주소)를 따로 읽는다. 못 읽은 쪽은 None.

    상호는 페이지 제목(Jina ``Title:``)이 우선이고, 없으면 본문 첫 줄이다. 주소는 '주소' 라벨 줄(또는
    그 다음 줄)이 우선이고, 없으면 시·도로 시작해 시군구·도로명이 이어지는 첫 줄이다. 본문 전체를
    한 덩어리로 보지 않는다 — 다른 줄에 우연히 든 병원명·지역명으로 신원을 확인하지 않는다.
    """

    name: str | None = None
    for raw in (markdown or "").splitlines():
        match = _TITLE_RE.match(raw.strip())
        if match:
            candidate = _TITLE_SUFFIX_RE.sub("", _plain_line(match.group(1))).strip()
            if candidate:
                name = candidate
                break
    lines = _content_lines(markdown)
    if name is None and lines:
        name = lines[0]

    address: str | None = None
    for index, line in enumerate(lines):
        label = _ADDRESS_LABEL_RE.match(line)
        if not label:
            continue
        value = label.group(1).strip() or (lines[index + 1] if index + 1 < len(lines) else "")
        if value and _looks_like_address(value):
            address = value
            break
    if address is None:
        address = next((line for line in lines if _looks_like_address(line)), None)
    return name, address


def _province_key(token: str) -> str | None:
    compact = _compact(token)
    if compact in _PROVINCE_LONG_NAMES:
        return _PROVINCE_LONG_NAMES[compact]
    if compact in _PROVINCES:
        return compact
    stem = _PROVINCE_UNIT_RE.sub("", compact)
    return stem if stem in _PROVINCES else None


def _local_keys(token: str) -> set[str]:
    """시·군·구·읍·면·동 토큰의 비교 키 — 이름 그대로와(두 글자 이상이면) 단위를 뺀 이름."""

    compact = _compact(token)
    keys = {compact}
    stem = re.sub(r"(?:시|군|구)$", "", compact)
    if len(stem) >= 2:
        keys.add(stem)
    return keys


def _hospital_region_scope(regions: Sequence[str]) -> dict[str, set[str]] | None:
    """병원 지역 태그 → {시·도: 시군구·동 키}. 시·도를 알 수 없으면 None(검색으로 확인할 수 없다).

    시·도가 없는 태그("중구")는 병원의 시·도가 하나뿐일 때만 그 시·도에 붙는다. 시·도가 여럿인데 어느
    쪽인지 모르는 태그가 있으면 확인하지 않는다(fail-closed).
    """

    pairs: list[tuple[str | None, set[str]]] = []
    for region in regions:
        words = [word for word in str(region).split() if _compact(word)]
        if not words:
            continue
        province = _province_key(words[0])
        locals_ = words[1:] if province is not None else words
        keys: set[str] = set()
        for word in locals_:
            keys |= {_compact(word)}
        pairs.append((province, keys))
    provinces = {province for province, _ in pairs if province is not None}
    if not provinces:
        return None
    scope: dict[str, set[str]] = {province: set() for province in provinces}
    for province, keys in pairs:
        if province is None:
            if len(provinces) != 1:
                return None
            province = next(iter(provinces))
        scope[province] |= keys
    return scope


def address_admin_tokens(address: str) -> list[str]:
    """주소 앞에서부터 이어지는 행정 토큰(시·도, 시·군·구, 읍·면·동). 도로명·번지·건물명에서 멈춘다.

    "부산 해운대구 센텀중앙로 97 서울빌딩" → ["부산", "해운대구"]. 건물명 '서울빌딩'은 지역 증거가 아니다.
    """

    tokens: list[str] = []
    for token in (address or "").split():
        if _province_key(token) is not None and not tokens:
            tokens.append(token)
            continue
        if _LOCAL_UNIT_RE.match(token) and _compact(token):
            tokens.append(token)
            continue
        break
    return tokens


def place_matches_hospital(markdown: str, name: str, regions: Sequence[str] = ()) -> bool:
    """검색으로 찾은 플레이스가 이 병원인지 — 상호가 정규화 동등이고 지역이 **주소**와 맞는가.

    상세 페이지에서 따로 읽은 상호가 병원명과 (공백·기호를 무시하고) 같아야 한다. 지점명은 병원 행의
    이름에 있을 때만 맞는다("다온의원 세종점" ≠ "다온의원"). 지역은 따로 읽은 주소의 행정 토큰과 대조한다.
    병원 행 지역에 시·도가 없거나 주소를 읽지 못하면 확인할 수 없으므로 거짓이다 — 저장된 place id가 없는 검색
    결과는 그때 UNVERIFIED로 남아 자동 적용되지 않는다.
    """

    place_name, address = parse_place_identity(markdown)
    compact_name = _compact(name)
    if not compact_name or not place_name or _compact(place_name) != compact_name:
        return False
    if not address:
        return False
    # 지역은 주소의 행정 토큰과만 정규화 동등으로 맞춘다 — 도로명·건물명의 부분 문자열은 증거가 아니다.
    # 지역 확인(fail-closed): 주소 첫 행정 토큰의 시·도가 병원 지역의 시·도와 같아야 하고, 병원 지역에
    # 시군구·동이 있으면 그중 하나가 주소의 시군구·동과도 같아야 한다. 같은 이름의 구(중구·동구·남구…)는
    # 시·도가 맞을 때만 증거다. 병원 지역에 시·도가 없으면 검색으로는 확인하지 않는다(저장 id만 믿는다).
    scope = _hospital_region_scope(regions)
    tokens = address_admin_tokens(address)
    if scope is None or not tokens:
        return False
    province = _province_key(tokens[0])
    if province is None or province not in scope:
        return False
    required = scope[province]
    if not required:
        return True
    address_keys: set[str] = set()
    for token in tokens[1:]:
        address_keys |= _local_keys(token)
    return any(_local_keys(word) & address_keys for word in required)


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
