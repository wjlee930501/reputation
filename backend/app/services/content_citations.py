"""AI 답변이 인용한 URL을 병원의 공개 표면(허브 페이지·개별 글)에 귀속한다.

왜 필요한가. `indexnow.py:3-10`의 프로덕션 실측이 말하는 인과는 하나다 —
**허브가 인용되면 93% 언급, 인용되지 않으면 4% 언급.** 즉 언급률은 인용률의
함수인데, `SovRecord.source_urls`는 저장만 되고 어떤 글이 인용됐는지 귀속하는
코드가 없었다. 그래서 "이번 달 우리 글이 AI 답변에 몇 번 인용됐는가"라는,
제품 가치에 가장 가까운 사실을 리포트가 말할 수 없었다.

이 모듈은 순수 함수만 둔다 (DB·네트워크 없음). 입력은 인용 URL 문자열,
병원 객체(slug·aeo_domain), 콘텐츠 아이템(id·title). 출력은 글 단위 귀속이다.

매칭 대상 표면(=병원이 실제로 서빙되는 호스트 형태 전부):
  1. 플랫폼 경로형   {SITE_BASE_URL host}/{slug}/...
  2. 플랫폼 서브도메인 {slug}.{SITE_BASE_URL host}/...
  3. 병원 자기 도메인  {aeo_domain}/...   (미들웨어가 루트를 /{slug}로 rewrite)

`reputation.motionlabs.kr.evil.com` 같은 유사 호스트는 호스트 **완전 일치**만
인정하므로 걸러진다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from app.core.config import settings

# 플랫폼 서브도메인에서 slug가 아닌 예약 라벨 (api/public/site.py와 동일 규칙).
_RESERVED_PLATFORM_LABELS = frozenset({"www", "admin", "api", "cname", "static", "assets"})

# 허브 페이지 첫 세그먼트 → 원장·AE가 읽는 라벨.
_HUB_PAGE_LABELS: Mapping[str, str] = {
    "": "병원 홈",
    "contents": "콘텐츠 목록",
    "doctor": "원장 소개",
    "treatments": "진료 안내",
    "visit": "진료 시간·오시는 길",
    "llms.txt": "AI 안내 파일",
}
HOME_PAGE_KEY = "home"

_MULTI_SLASH = re.compile(r"/{2,}")


@dataclass(frozen=True, slots=True)
class NormalizedUrl:
    """비교 가능한 형태로 정규화한 인용 URL — 스킴·www·쿼리·프래그먼트 제거."""

    host: str
    path: str


@dataclass(frozen=True, slots=True)
class SurfaceRoot:
    """병원 공개 표면의 (호스트, 기준 경로). 기준 경로가 비면 호스트 전체가 병원 것."""

    host: str
    base_path: str


@dataclass(frozen=True, slots=True)
class CitationMatch:
    """인용 URL 귀속 결과.

    - ``contents``: 콘텐츠 id → 그 글을 가리킨 원본 인용 URL들
    - ``hub_pages``: 허브 페이지 키 → 원본 인용 URL들
    - ``unresolved``: 우리 표면이지만 아는 글로 해석되지 않은 URL (예: 지난 기간 글)
    """

    contents: dict[str, list[str]]
    hub_pages: dict[str, list[str]]
    unresolved: list[str]

    @property
    def owned_url_count(self) -> int:
        return (
            sum(len(urls) for urls in self.contents.values())
            + sum(len(urls) for urls in self.hub_pages.values())
            + len(self.unresolved)
        )

    @property
    def has_owned(self) -> bool:
        return bool(self.contents or self.hub_pages or self.unresolved)


def platform_site_host() -> str:
    """공개 표면 기본 호스트 (예: reputation.motionlabs.kr). SITE_BASE_URL에서 파생."""
    return (urlparse(settings.SITE_BASE_URL).hostname or "").lower().removeprefix("www.")


def platform_subdomain_host(slug: str | None) -> str | None:
    """기본 서브도메인 호스트 {slug}.{platform host}. 만들 수 없으면 None."""
    base = platform_site_host()
    label = (slug or "").strip().lower()
    if not base or not label or "." in label or label in _RESERVED_PLATFORM_LABELS:
        return None
    return f"{label}.{base}"


def platform_public_base_url(slug: str | None) -> str | None:
    """플랫폼 기본 서브도메인의 절대 base URL. tasks._public_site_url이 재사용한다."""
    host = platform_subdomain_host(slug)
    return f"https://{host}/" if host else None


def _host_of(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    candidate = value.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate.lstrip('/')}"
    return (urlparse(candidate).hostname or "").lower().rstrip(".").removeprefix("www.")


def hospital_surface_roots(hospital: Any) -> tuple[SurfaceRoot, ...]:
    """병원이 실제로 서빙되는 (호스트, 기준 경로) 전부. 중복은 제거한다."""
    if hospital is None:
        return ()
    slug = (getattr(hospital, "slug", None) or "").strip().lower()
    roots: list[SurfaceRoot] = []
    seen: set[tuple[str, str]] = set()

    def _add(host: str, base_path: str) -> None:
        if not host:
            return
        key = (host, base_path)
        if key in seen:
            return
        seen.add(key)
        roots.append(SurfaceRoot(host=host, base_path=base_path))

    custom_host = _host_of(getattr(hospital, "aeo_domain", None))
    _add(custom_host, "")
    subdomain = platform_subdomain_host(slug)
    if subdomain:
        _add(subdomain, "")
    platform_host = platform_site_host()
    if platform_host and slug:
        _add(platform_host, f"/{slug}")
    return tuple(roots)


def platform_owned_source_roots(hospital: Any) -> set[tuple[str, str]]:
    """`report_engine._owned_source_roots`가 합칠 (host, path) 튜플 집합.

    플랫폼 경로형·서브도메인형을 owned 후보에 넣지 않으면, 자기 도메인 없이
    기본 주소로 서빙되는 병원은 허브가 인용돼도 owned=0으로 집계된다.
    """
    return {
        (root.host, root.base_path)
        for root in hospital_surface_roots(hospital)
        # 자기 도메인은 report_engine이 이미 후보로 넣는다(중복돼도 무해).
    }


def normalize_cited_url(value: Any) -> NormalizedUrl | None:
    """인용 URL을 (host, path)로 정규화한다.

    스킴·www.·포트·쿼리·프래그먼트·끝 슬래시를 제거하고 percent-encoding을 푼다
    (한글 진료 pillar 경로가 인코딩된 형태로 인용되는 경우가 있다).
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw.lstrip('/')}"
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".").removeprefix("www.")
    if not host:
        return None
    path = unquote(parsed.path or "/")
    path = _MULTI_SLASH.sub("/", path)
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > 1:
        path = path.rstrip("/")
    return NormalizedUrl(host=host, path=path or "/")


def _relative_path(url: NormalizedUrl, roots: Sequence[SurfaceRoot]) -> str | None:
    """URL이 병원 표면 안이면 기준 경로 이후의 상대 경로를, 아니면 None."""
    for root in roots:
        if url.host != root.host:
            continue
        if root.base_path:
            if url.path != root.base_path and not url.path.startswith(f"{root.base_path}/"):
                continue
            return url.path[len(root.base_path) :].strip("/")
        return url.path.strip("/")
    return None


def hub_page_label(page_key: str) -> str:
    """허브 페이지 키의 표시 라벨. 알 수 없는 경로는 경로 자체를 보여준다."""
    if page_key == HOME_PAGE_KEY:
        return _HUB_PAGE_LABELS[""]
    head = page_key.split("/", 1)[0]
    label = _HUB_PAGE_LABELS.get(head)
    if label is None:
        return f"/{page_key}"
    if "/" in page_key:
        return f"{label} · {page_key.split('/', 1)[1]}"
    return label


def _content_index(content_items: Iterable[Any]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for item in content_items or ():
        identifier = getattr(item, "id", None)
        if identifier is None:
            continue
        index[str(identifier).strip().lower()] = item
    return index


def build_citation_match(
    source_urls: Iterable[str],
    hospital: Any,
    content_items: Iterable[Any] = (),
) -> CitationMatch:
    """인용 URL 목록을 글 / 허브 페이지 / 미해석 버킷으로 나눈다."""
    roots = hospital_surface_roots(hospital)
    contents: dict[str, list[str]] = {}
    hub_pages: dict[str, list[str]] = {}
    unresolved: list[str] = []
    if not roots:
        return CitationMatch(contents=contents, hub_pages=hub_pages, unresolved=unresolved)

    known = _content_index(content_items)
    seen: set[str] = set()
    for raw in source_urls or ():
        if not isinstance(raw, str) or not raw.strip():
            continue
        original = raw.strip()
        normalized = normalize_cited_url(original)
        if normalized is None:
            continue
        dedupe_key = f"{normalized.host}{normalized.path}"
        if dedupe_key in seen:
            continue
        relative = _relative_path(normalized, roots)
        if relative is None:
            continue
        seen.add(dedupe_key)
        segments = [segment for segment in relative.split("/") if segment]
        if len(segments) >= 2 and segments[0] == "contents":
            content_key = segments[1].strip().lower()
            if content_key in known:
                contents.setdefault(content_key, []).append(original)
            else:
                # 우리 표면이 맞지만 넘겨받은 글 목록에 없다(다른 기간 글 등).
                unresolved.append(original)
            continue
        page_key = relative or HOME_PAGE_KEY
        hub_pages.setdefault(page_key, []).append(original)
    return CitationMatch(contents=contents, hub_pages=hub_pages, unresolved=unresolved)


def match_cited_content(
    source_urls: Iterable[str],
    hospital: Any,
    content_items: Iterable[Any] = (),
) -> dict[str, list[str]]:
    """인용 URL → {콘텐츠 id: 그 글을 가리킨 URL들}. 허브 페이지는 제외한다."""
    return build_citation_match(source_urls, hospital, content_items).contents


def match_cited_hub_pages(
    source_urls: Iterable[str],
    hospital: Any,
) -> dict[str, list[str]]:
    """인용 URL → {허브 페이지 키: URL들}. 개별 글은 제외한다."""
    return build_citation_match(source_urls, hospital, ()).hub_pages
