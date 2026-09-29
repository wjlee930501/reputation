"""참고자료 URL 검증 — 실제로 열어 본 문서의 제목·본문만으로 판정하고 그 결과를 남긴다.

2026-09-29 전수 점검(공개 참고자료 365건)에서 정상 48건은 전부 사람이 확인한 수기 목록
(`CURATED_MEDICAL_SOURCE_PAGES`)이었고, 빈 페이지·죽은 링크·주제 불일치는 전부 목록 밖의
URL이었다. 모델이 KDCA `cntnts_sn`, 국가암정보센터 메뉴 코드, 아산 `contentId`를 추측해
쓰고, 옛 검증은 (a) 존재를 404/410으로만 봤고 (b) 주제 일치를 **모델 자신이 쓴 라벨**로
판정했다. 이 모듈이 정하는 계약은 다음과 같다.

1. **수기 목록이 기본이다.** 목록 URL은 사람이 제목·주소를 확인한 문서다. 주제 판정은
   카탈로그의 키워드·확인된 제목과 글 주제를 대조하고, 접속 확인이 가능하면 문서가 사라지지
   않았는지(404·빈 템플릿·홈 리다이렉트)만 본다. 접속이 판단 불가(403/429/시간 초과/오프라인)면
   목록 URL은 남긴다.
2. **목록 밖 URL은 실제 GET이 통과해야만 남는다.** 200, 리다이렉트 뒤에도 화이트리스트,
   같은 도메인 홈·오류 페이지로의 soft-404가 아님, 빈 템플릿·통계·메뉴 페이지가 아님, 그리고
   **실제 문서 제목·본문**이 글 주제와 맞을 것. 판단 불가(403/429/시간 초과/영문 전용·기관명뿐인
   제목/오프라인)는 제거다.
3. **모델 라벨(reference title)은 주제 판정에 쓰지 않는다.** 라벨은 모델이 글 주제어로
   쓰므로 자기 확인이 된다.
4. **GET은 한도가 있다.** 요청당 시간 제한, 전체·도메인별 동시성, 도메인 간격, 실행당 GET
   상한을 두고, httpx의 모든 예외를 잡아 URL 하나가 생성·발행 전체를 멈추지 못하게 한다.
5. **기관 사이트 장애가 대량 보류가 되지 않는다.** 발행 직전 재검증에서 도메인이 내려가
   있으면(연결 오류·시간 초과·5xx) 같은 URL·같은 글 주제의 직전 통과 판정을 정해진 기간 안에서
   재사용하고, 재사용할 통과가 없는 목록 밖 URL은 **제거하지 않고 미룬다**(`defer_transient`).
   생성 단계는 미루지 않는다 — 판단 불가인 목록 밖 URL은 제거하고 수기 목록으로 채운다.
6. **사람이 확인해 제외한 주소는 어떤 경로로도 남지 않는다.** `REFERENCE_URL_EXCLUSIONS`
   (authority_sources)에 있는 주소는 GET·직전 통과 재사용 전에 `excluded_source`로 떨어지고,
   저장된 통과 기록도 게이트에서 인정되지 않는다. 비교는 정규화한 주소로 한다.
7. **통과 판정은 URL과 글 주제에 함께 묶인다.** 기록마다 판정에 쓴 글 주제어의 지문
   (`topic_fingerprint`)을 남기고, 지금 글의 주제 지문과 다르면 신선한 통과로 보지 않는다 —
   제목·본문·FAQ·brief가 바뀌면 같은 URL이라도 다시 판정한다.

결과는 `content_items.reference_checks`(0082)에 참고자료마다 한 건씩 남고, 발행 게이트는
**같은 URL**(지문)·**같은 글 주제**(지문)의 **신선한 통과** 판정이 모든 참고자료에 있을 때만
공개한다. 형식이 깨진 참고자료 항목(매핑이 아님·빈 주소)도 게이트를 통과하지 못한다.

운영 외 환경의 기본 fetcher는 네트워크를 쓰지 않는다(`OfflineReferenceFetcher`). 그래서
개발·테스트 환경에서 목록 밖 URL은 "검증하지 못함"으로 **제거**된다 — 옛 코드가 운영에서만
GET하고 나머지 환경에서는 조용히 통과시켜 이 결함을 숨겼던 것을 되풀이하지 않는다. 테스트는
`override_reference_fetcher`로 가짜 fetcher를 주입한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import logging
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.content_similarity import (
    REFERENCE_TOKEN_MATCH_MIN,
    normalize_topic_text,
    reference_topic_match,
)
from app.utils.authority_sources import (
    CURATED_MEDICAL_SOURCE_PAGES,
    curated_source_entries,
    institution_title_tokens,
    is_citable_reference_url,
    is_curated_source_url,
    is_whitelisted_url,
    keyword_names_provider,
    reference_exclusion_reason,
    select_curated_authority_sources,
)

logger = logging.getLogger(__name__)

# ── 한도·기간(이름 있는 상수 — 테스트와 운영 문서가 같은 값을 가리킨다) ─────────────────
# 발행 게이트가 받아들이는 통과 판정의 최대 나이. 생성 시 검증이 기본이고, 23:00 D-2
# 생성분(발행까지 약 33시간)은 07:45 게이트가 다시 확인한다 — 08:00에 GET이 몰리지 않는다.
REFERENCE_CHECK_MAX_AGE = timedelta(hours=24)
# 도메인 장애 시 직전 통과 판정을 재사용할 수 있는 기간. 기준은 마지막 **실제** 확인 시각
# (`verified_at`)이라 재사용을 거듭해도 이 기간을 넘겨 늘어나지 않는다.
REFERENCE_CHECK_REUSE_WINDOW = timedelta(days=7)
# 저장된 시각이 현재보다 이만큼까지 앞서 있는 것은 서버 간 시계 차이로 보고 받아들인다. 더 앞선
# 미래 시각의 기록은 신선한 통과로도, 재사용할 통과로도 보지 않는다(영원히 '신선'해지는 것을 막는다).
REFERENCE_CHECK_CLOCK_SKEW = timedelta(minutes=5)
# 요청 하나의 전체 시간 상한(연결·읽기 포함). 초과하면 판단 불가다.
REFERENCE_FETCH_TIMEOUT_SECONDS = 8.0
# 한 번의 검증 호출 안에서 동시에 열어 두는 요청 수.
REFERENCE_FETCH_MAX_CONCURRENCY = 4
# 같은 도메인에는 한 번에 한 요청만, 요청 사이 간격을 둔다(기관 사이트에 부담을 주지 않는다).
REFERENCE_FETCH_PER_DOMAIN_CONCURRENCY = 1
REFERENCE_FETCH_DOMAIN_SPACING_SECONDS = 0.5
# 발행기·07:45 게이트 한 번의 실행이 쓸 수 있는 GET 수. 넘으면 남은 글은 다음 시간대로 미룬다.
REFERENCE_FETCH_MAX_PER_RUN = 60
# 생성 1회의 GET 상한 — 모델 참고자료 최대 5개 + 수기 목록 치유 후보 최대 3개.
REFERENCE_GENERATION_MAX_FETCHES = 8
REFERENCE_FETCH_MAX_REDIRECTS = 5
# 본문은 이만큼만 읽는다(제목·본문 판정에 충분하고 거대한 응답이 메모리를 채우지 않는다).
REFERENCE_FETCH_MAX_BYTES = 1_500_000
# 스크립트·스타일을 뺀 본문 텍스트가 이보다 짧으면 빈 템플릿이다.
REFERENCE_MIN_BODY_CHARS = 200
# 판정·저장되는 제목 길이 상한.
REFERENCE_PAGE_TITLE_MAX_CHARS = 200

# 2: 글 주제 지문(`topic_fingerprint`)과 미룸 판정(`deferred`)이 더해졌다.
REFERENCE_CHECKS_SCHEMA_VERSION = 2

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
# 발행 직전 재검증에서 기관 사이트 일시 장애로 판정을 미룬 기록. 통과도 제거도 아니다.
VERDICT_DEFERRED = "deferred"

# 통과 사유
REASON_PAGE_VERIFIED = "page_verified"
REASON_CURATED_VERIFIED = "curated_verified"
REASON_CURATED_UNREACHABLE = "curated_unreachable"
REASON_REUSED_PREVIOUS_PASS = "reused_previous_pass"
# 제거 사유
REASON_NOT_CITABLE = "not_citable"
REASON_DEAD_LINK = "dead_link"
REASON_HTTP_ERROR = "http_error"
REASON_REDIRECT_OUTSIDE = "redirect_outside_whitelist"
REASON_SOFT_404 = "soft_404_redirect"
REASON_ERROR_PAGE = "error_page"
REASON_EMPTY_TEMPLATE = "empty_template"
REASON_STATS_OR_MENU = "stats_or_menu_page"
REASON_UNRELATED = "unrelated_topic"
REASON_UNDETERMINABLE = "undeterminable"
REASON_NOT_VERIFIED = "not_verified"
# 미룸 사유 — 목록 밖 URL의 기관 사이트에 일시적으로 접속하지 못했다(재사용할 직전 통과도 없음).
REASON_SITE_UNREACHABLE = "site_unreachable"
# 게이트 전용 — 참고자료 항목의 형식이 깨졌다(매핑이 아님·빈 주소).
REASON_MALFORMED_ENTRY = "malformed_entry"
# 사람이 실제 GET으로 확인해 제외한 주소(`REFERENCE_URL_EXCLUSIONS`).
REASON_EXCLUDED_SOURCE = "excluded_source"

PASS_REASONS = frozenset(
    {
        REASON_PAGE_VERIFIED,
        REASON_CURATED_VERIFIED,
        REASON_CURATED_UNREACHABLE,
        REASON_REUSED_PREVIOUS_PASS,
    }
)

# 운영자·작가에게 보이는 사유(한국어). 관리자 PATCH 거절 문구와 재작성 지적이 같은 말을 쓴다.
REASON_LABELS: Mapping[str, str] = {
    REASON_PAGE_VERIFIED: "실제 문서 확인",
    REASON_CURATED_VERIFIED: "검증된 문서 목록",
    REASON_CURATED_UNREACHABLE: "검증된 문서 목록(접속 확인 불가)",
    REASON_REUSED_PREVIOUS_PASS: "기관 사이트 장애로 직전 확인 결과 사용",
    REASON_NOT_CITABLE: "허용된 공신력 기관의 문서 주소가 아님",
    REASON_DEAD_LINK: "문서가 없음(404/410)",
    REASON_HTTP_ERROR: "문서를 열 수 없음(HTTP 오류)",
    REASON_REDIRECT_OUTSIDE: "허용된 기관 밖으로 이동함",
    REASON_SOFT_404: "없는 문서라 기관 홈·오류 페이지로 이동함",
    REASON_ERROR_PAGE: "오류 안내 페이지",
    REASON_EMPTY_TEMPLATE: "제목·본문이 빈 페이지",
    REASON_STATS_OR_MENU: "통계·메뉴 페이지",
    REASON_UNRELATED: "이 글의 주제와 다른 문서",
    REASON_UNDETERMINABLE: "실제 문서 내용을 확인할 수 없음",
    REASON_NOT_VERIFIED: "주소를 실제로 열어 확인하지 못함",
    REASON_SITE_UNREACHABLE: "기관 사이트에 접속하지 못함(일시 장애)",
    REASON_MALFORMED_ENTRY: "참고 자료 항목 형식 오류",
    REASON_EXCLUDED_SOURCE: "검수에서 근거로 쓸 수 없다고 확인된 주소(빈 페이지)",
}

# fetcher가 돌려주는 오류 분류
FETCH_ERROR_TIMEOUT = "timeout"
FETCH_ERROR_CONNECT = "connect"
FETCH_ERROR_NETWORK = "network"
FETCH_ERROR_PROTOCOL = "protocol"
FETCH_ERROR_TOO_MANY_REDIRECTS = "too_many_redirects"
FETCH_ERROR_INVALID_URL = "invalid_url"
FETCH_ERROR_DECODE = "decode"
FETCH_ERROR_OTHER = "error"
FETCH_ERROR_OFFLINE = "offline"
FETCH_ERROR_DOMAIN_DOWN = "domain_down"
FETCH_ERROR_BUDGET = "budget_exhausted"

# 이 오류(또는 5xx)가 난 도메인은 이번 실행 동안 내려가 있다고 본다.
_DOMAIN_DOWN_ERRORS = frozenset(
    {
        FETCH_ERROR_TIMEOUT,
        FETCH_ERROR_CONNECT,
        FETCH_ERROR_NETWORK,
        FETCH_ERROR_PROTOCOL,
        FETCH_ERROR_DOMAIN_DOWN,
    }
)
# 발행 직전 재검증이 제거 대신 미루는 일시 장애. 404/410·soft-404·빈 템플릿·통계·메뉴·
# 주제 불일치·화이트리스트 이탈·그 밖의 4xx는 확정 판정이라 여기에 없다.
_TRANSIENT_FETCH_ERRORS = _DOMAIN_DOWN_ERRORS | {FETCH_ERROR_OTHER}
_TRANSIENT_HTTP_STATUSES = frozenset({408, 429})


def is_transient_fetch(fetched: "FetchResult") -> bool:
    """다음 시간대에 다시 열어 볼 만한 일시 장애인가(연결·시간 초과·프로토콜·5xx·408/429)."""

    if fetched.error is not None:
        return fetched.error in _TRANSIENT_FETCH_ERRORS
    status = fetched.status
    return status is not None and (status >= 500 or status in _TRANSIENT_HTTP_STATUSES)


@dataclass(frozen=True, slots=True)
class FetchResult:
    """GET 한 번의 관측. 예외는 `error`로만 표현한다(호출부로 새지 않는다)."""

    url: str
    status: int | None = None
    final_url: str | None = None
    html: str = ""
    error: str | None = None


class ReferenceFetcher(Protocol):
    async def __call__(self, url: str) -> FetchResult: ...


def _classify_fetch_exception(exc: BaseException) -> str:
    """httpx 예외 계층을 운영 판정용 오류 분류로 줄인다. 어떤 예외든 분류된다."""

    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        return FETCH_ERROR_TIMEOUT
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return FETCH_ERROR_INVALID_URL
    if isinstance(exc, httpx.TooManyRedirects):
        return FETCH_ERROR_TOO_MANY_REDIRECTS
    if isinstance(exc, httpx.ConnectError):
        return FETCH_ERROR_CONNECT
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.ProtocolError)):
        return FETCH_ERROR_PROTOCOL
    if isinstance(exc, (httpx.NetworkError, httpx.TransportError, OSError)):
        return FETCH_ERROR_NETWORK
    if isinstance(exc, (httpx.DecodingError, UnicodeError)):
        return FETCH_ERROR_DECODE
    if isinstance(exc, ValueError):
        return FETCH_ERROR_INVALID_URL
    return FETCH_ERROR_OTHER


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
}


class HttpxReferenceFetcher:
    """운영 fetcher — 리다이렉트를 따라가되 본문은 상한까지만 읽는다."""

    def __init__(
        self,
        *,
        timeout: float = REFERENCE_FETCH_TIMEOUT_SECONDS,
        max_bytes: int = REFERENCE_FETCH_MAX_BYTES,
        max_redirects: int = REFERENCE_FETCH_MAX_REDIRECTS,
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    async def __call__(self, url: str) -> FetchResult:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
                max_redirects=self._max_redirects,
                headers=_BROWSER_HEADERS,
            ) as client:
                async with client.stream("GET", url) as response:
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= self._max_bytes:
                            break
                    raw = b"".join(chunks)[: self._max_bytes]
                    encoding = response.charset_encoding or "utf-8"
                    try:
                        text = raw.decode(encoding, errors="replace")
                    except LookupError:
                        text = raw.decode("utf-8", errors="replace")
                    return FetchResult(
                        url=url,
                        status=response.status_code,
                        final_url=str(response.url),
                        html=text,
                    )
        except Exception as exc:  # noqa: BLE001 — URL 하나가 생성·발행을 멈추면 안 된다.
            error = _classify_fetch_exception(exc)
            logger.info(
                "reference fetch failed host=%s error=%s type=%s",
                _host(url),
                error,
                type(exc).__name__,
            )
            return FetchResult(url=url, error=error)


class OfflineReferenceFetcher:
    """네트워크를 쓰지 않는 fetcher — 운영 외 환경의 기본값.

    목록 밖 URL은 "검증하지 못함"으로 제거되고, 수기 목록 URL은 카탈로그 주제 대조만으로
    남는다. 옛 코드처럼 운영 외 환경에서 검증을 건너뛰어 목록 밖 URL을 통과시키지 않는다.
    """

    async def __call__(self, url: str) -> FetchResult:
        return FetchResult(url=url, error=FETCH_ERROR_OFFLINE)


_fetcher_override: ReferenceFetcher | None = None


@contextmanager
def override_reference_fetcher(fetcher: ReferenceFetcher) -> Iterator[ReferenceFetcher]:
    """테스트·점검 스크립트가 가짜 fetcher를 주입한다(실제 네트워크 없이)."""

    global _fetcher_override
    previous = _fetcher_override
    _fetcher_override = fetcher
    try:
        yield fetcher
    finally:
        _fetcher_override = previous


def default_reference_fetcher() -> ReferenceFetcher:
    """주입된 fetcher → 운영이면 실제 GET → 그 밖에는 오프라인."""

    if _fetcher_override is not None:
        return _fetcher_override
    if str(settings.APP_ENV or "").lower() == "production":
        return HttpxReferenceFetcher()
    return OfflineReferenceFetcher()


# ── URL 신원 ────────────────────────────────────────────────────────────────


def reference_url_fingerprint(url: object) -> str:
    """검증 결과와 참고자료를 묶는 URL 지문. 정확히 같은 주소일 때만 같다."""

    return hashlib.sha256(str(url or "").strip().encode("utf-8")).hexdigest()[:32]


def _host(url: object) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


# ── HTML → 제목·본문 ─────────────────────────────────────────────────────────

_TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_STRIP_BLOCKS = re.compile(
    r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


def html_page_title(html: str) -> str:
    match = _TITLE_PATTERN.search(html or "")
    if not match:
        return ""
    title = html_lib.unescape(_TAGS.sub(" ", match.group(1)))
    return _SPACES.sub(" ", title).strip()[:REFERENCE_PAGE_TITLE_MAX_CHARS]


def html_body_text(html: str) -> str:
    """스크립트·스타일·주석과 `<head>`를 뺀 보이는 텍스트."""

    text = _COMMENTS.sub(" ", html or "")
    text = _STRIP_BLOCKS.sub(" ", text)
    text = re.sub(r"<head\b[^>]*>.*?</head\s*>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = html_lib.unescape(_TAGS.sub(" ", text))
    return _SPACES.sub(" ", text).strip()


# ── 페이지 판정 규칙 ─────────────────────────────────────────────────────────

# 문서 제목이 `<문서명> | <사이트> | <기관>` 모양인 기관. 첫 칸이 비면 없는 문서의 빈
# 템플릿이다(KDCA는 없는 cntnts_sn에도 200과 `| 국가건강정보포털 | 질병관리청`을 준다,
# 아산은 없는 contentId에 `() | 질환백과 | …`를 준다 — 2026-09-29 점검의 실제 HTML).
_DOCUMENT_TITLE_FIRST_DOMAINS = ("health.kdca.go.kr", "amc.seoul.kr")
_CANCER_DOMAIN = "cancer.go.kr"
# 국가암정보센터 메뉴 콘텐츠 경로. 모델이 추측한 메뉴 코드(S1T…C…)의 모양이다.
_CANCER_MENU_PATH = re.compile(r"^/lay\d+/S\d+T\d+C\d+/contents\.do$", re.IGNORECASE)
# 목록·메뉴 진입 파일 이름(기관 공통).
_LIST_OR_MENU_FILE = re.compile(r"(list|dummy|menu|sitemap)\.(do|jsp|es|html?)$", re.IGNORECASE)
_STATS_BREADCRUMBS = ("통계로 보는 암", "암 통계", "통계정보", "통계자료")
_ERROR_TITLE_PATTERNS = (
    "알림메세지",
    "알림메시지",
    "오류",
    "에러",
    "error",
    "not found",
    "페이지를 찾을 수 없",
    "존재하지 않",
    "access denied",
    "egovframe",
    "forbidden",
)
_SOFT_404_FILE = re.compile(
    r"(^|/)(main|index|home|intro|error|err|notfound|not_found|404|default)(\.[a-z]+)?$",
    re.IGNORECASE,
)
_PARENTHETICAL = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_HANGUL = re.compile(r"[가-힣]")


def _is_soft_404(requested: str, final: str) -> bool:
    """같은 도메인 안에서 홈·오류·메인 화면으로 이동했는가.

    없는 문서 번호를 홈으로 돌려보내는 기관이 많다(KDCA `…/main.do`, `/healthinfo/`).
    최종 호스트만 보면 통과하므로 경로 모양을 본다.
    """

    try:
        req = urlparse(requested)
        fin = urlparse(final)
    except ValueError:
        return True
    if (req.path.rstrip("/"), req.query) == (fin.path.rstrip("/"), fin.query):
        return False
    if req.path.endswith("/") and fin.path.startswith(req.path) and fin.query == req.query:
        # 디렉터리 주소가 그 아래 문서(index.html 등)로 이어진 것은 정상 이동이다.
        return False
    path = fin.path or "/"
    if path in {"", "/"}:
        return True
    if _SOFT_404_FILE.search(path.rstrip("/")):
        return True
    if path.endswith("/") and path.count("/") <= 2 and not fin.query:
        # `/healthinfo/` 같은 섹션 첫 화면.
        return True
    if req.query and not fin.query and fin.path != req.path:
        # 문서를 가리키던 식별 질의값이 사라진 채 다른 경로로 갔다.
        return True
    return False


def _breadcrumb(title: str) -> list[str] | None:
    stripped = title.strip()
    if stripped.startswith("홈") and ">" in stripped:
        return [segment.strip() for segment in stripped.split(">") if segment.strip()]
    return None


def _institution_only(text: str) -> bool:
    tokens = [
        normalize_topic_text(token)
        for token in re.split(r"[^0-9A-Za-z가-힣]+", text or "")
        if normalize_topic_text(token)
    ]
    if not tokens:
        return True
    ignored = institution_title_tokens() | {
        "국가건강정보포털",
        "건강정보",
        "질환백과",
        "의료정보",
        "서울아산병원",
        "국가암정보센터",
        "홈",
    }
    return all(token in ignored for token in tokens)


def page_topic(title: str, host: str) -> str:
    """실제 문서 제목에서 주제 부분만 뽑는다. 뽑을 수 없으면 빈 문자열."""

    if not title:
        return ""
    crumbs = _breadcrumb(title)
    if crumbs:
        return crumbs[-1]
    parts = [part.strip() for part in re.split(r"\s*[|<]\s*|\s+[-–—:]\s+", title) if part]
    for part in parts:
        core = _PARENTHETICAL.sub(" ", part).strip()
        if not core or _institution_only(core):
            continue
        return core
    return ""


def _document_title_segment(title: str) -> str:
    first = (title or "").split("|", 1)[0]
    return _PARENTHETICAL.sub(" ", first).strip()


def _is_stats_or_menu(url: str, final_url: str, title: str, *, curated: bool) -> bool:
    host = _host(final_url or url)
    crumbs = _breadcrumb(title) or []
    if _host_matches(host, _CANCER_DOMAIN) and any(
        marker in crumb for crumb in crumbs for marker in _STATS_BREADCRUMBS
    ):
        return True
    if curated:
        # 수기 목록은 사람이 문서임을 확인한 주소다(국가암검진 안내 메뉴 페이지 포함).
        return False
    for candidate in (url, final_url):
        if not candidate:
            continue
        try:
            path = urlparse(candidate).path or ""
        except ValueError:
            return True
        if _host_matches(_host(candidate), _CANCER_DOMAIN) and _CANCER_MENU_PATH.match(path):
            return True
        if _LIST_OR_MENU_FILE.search(path):
            return True
    return False


def _is_error_title(title: str) -> bool:
    lowered = (title or "").lower()
    return any(pattern in lowered for pattern in _ERROR_TITLE_PATTERNS)


def _is_empty_template(title: str, body_text: str, host: str) -> bool:
    if any(_host_matches(host, domain) for domain in _DOCUMENT_TITLE_FIRST_DOMAINS):
        segment = _document_title_segment(title)
        if not re.search(r"[0-9A-Za-z가-힣]", segment):
            return True
    return len(body_text) < REFERENCE_MIN_BODY_CHARS


# ── 글 주제 ─────────────────────────────────────────────────────────────────


def article_topic_terms(
    *,
    title: object = None,
    body: object = None,
    content_brief: Mapping[str, Any] | None = None,
    faq_question: object = None,
    extra: Iterable[object] = (),
) -> list[str]:
    """참고자료 적합성의 기준이 되는 '이 글의 주제어'.

    본문 전체를 쓰지 않는다 — 제목·첫 H2·승인된 측정 키워드·질의·질의 대상 이름·진료 서사만
    쓴다. 병원 단위 문구(must_use_messages)나 진료과 전체 문구는 글의 주제가 아니다.
    """

    terms: list[str] = [str(title or "")]
    first_h2 = re.search(r"^##\s+(.+)$", str(body or ""), flags=re.MULTILINE)
    if first_h2:
        terms.append(first_h2.group(1))
    brief = content_brief or {}
    terms.append(str(brief.get("target_keyword") or ""))
    terms.append(str(brief.get("target_query") or ""))
    query_target = brief.get("query_target")
    if isinstance(query_target, Mapping):
        terms.append(str(query_target.get("name") or ""))
    narrative = brief.get("treatment_narrative")
    if isinstance(narrative, Mapping):
        terms.append(str(narrative.get("treatment") or ""))
        terms.append(str(narrative.get("angle") or ""))
    elif narrative:
        terms.append(str(narrative))
    if faq_question:
        terms.append(str(faq_question))
    terms.extend(str(value) for value in extra if value)
    return [term for term in terms if term.strip()]


def item_topic_terms(item: object) -> list[str]:
    brief = getattr(item, "content_brief", None)
    return article_topic_terms(
        title=getattr(item, "title", None),
        body=getattr(item, "body", None),
        content_brief=brief if isinstance(brief, Mapping) else None,
        faq_question=getattr(item, "faq_question", None),
    )


def topic_fingerprint(topic_terms: Iterable[object]) -> str:
    """관련성 판정에 쓴 글 주제어의 지문. 정규화한 주제어 집합이 같을 때만 같다."""

    normalized = sorted(
        {normalize_topic_text(term) for term in topic_terms if normalize_topic_text(term)}
    )
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()[:32]


def item_topic_fingerprint(item: object) -> str:
    """지금 행의 제목·첫 H2·FAQ 질문·brief에서 계산한 주제 지문(저장값이 아니다)."""

    return topic_fingerprint(item_topic_terms(item))


def _curated_entries(url: str) -> list[Mapping[str, object]]:
    # 같은 문서의 별칭(`utm_source`·`:443`·`cntnts_sn=03796` …)도 그 목록 항목이다. 반복된 id는
    # 서버가 쓰는 첫 값만 본다 — 목록 문서로 인정해 주는 판정이기 때문이다.
    return list(curated_source_entries(url))


def _serves_curated_document(url: object) -> bool:
    """서버가 이 주소로 수기 목록 문서를 돌려주는가(반복 id는 첫 값) — 검증기의 `curated`.

    카탈로그 주제 대조·장애 시 유지처럼 목록 문서라서 남기는 쪽의 판정이다. 진료비·병원 선택
    글에서 빼는 쪽은 어느 값이든 목록 문서면 빼는 `is_curated_source_url`(`names_curated_document`).
    """
    return bool(curated_source_entries(url))


def _curated_title_topic(title: object) -> str:
    text = str(title or "")
    for separator in ("—", " - ", "–"):
        if separator in text:
            return text.split(separator, 1)[1].strip()
    return text


def curated_topic_relevant(url: str, topic_terms: Sequence[str]) -> bool:
    """수기 목록 문서가 이 글의 주제인가 — 카탈로그 키워드·확인된 제목으로만 판정한다.

    진료과 이름·병원 고르기 경로 키워드(`keyword_names_provider`: 정형외과·병원선택·통증종류 …)는
    주제가 아니다 — 그것만 겹친 '정형외과 병원 추천' 글이 요통·디스크 문서로 통과하지 않는다.
    제목 대조의 카탈로그 제목은 질환·시술 이름뿐이다(기관명은 빼고 본다).
    """

    entries = _curated_entries(url)
    if not entries or not topic_terms:
        return False
    joined = normalize_topic_text(" ".join(topic_terms))
    for entry in entries:
        keywords = entry.get("keywords") or ()
        if any(
            normalize_topic_text(keyword) in joined
            for keyword in keywords
            if keyword and not keyword_names_provider(keyword)
        ):
            return True
        score = reference_topic_match(
            _curated_title_topic(entry.get("title")),
            list(topic_terms),
            ignored_tokens=institution_title_tokens(),
        )
        if score is not None and score >= REFERENCE_TOKEN_MATCH_MIN:
            return True
    return False


def curated_sources_for_topic(
    topic_terms: Sequence[str], *, limit: int = 3, exclude_urls: Iterable[str] = ()
) -> list[dict[str, str]]:
    """글 주제로 채점해 통과한 수기 목록 문서(프롬프트 후보·치유용).

    채점은 카탈로그 키워드가 이 글의 주제어 안에 있는가다(`select_curated_authority_sources`).
    병원 전체 진료 문구로 고르지 않는다 — 호출부가 글 자신의 주제만 넘긴다.
    """

    excluded = {str(url) for url in exclude_urls}
    text = " ".join(str(term) for term in topic_terms if term)
    candidates = select_curated_authority_sources(
        text, limit=len(CURATED_MEDICAL_SOURCE_PAGES)
    )
    return [source for source in candidates if source["url"] not in excluded][:limit]


# 검사·시술 이름에 붙는 일반 형태소. '뇌초음파검사'와 '복부초음파 검사'는 bigram이 대부분
# 겹치지만 다른 문서다 — 부위·질환을 가리키는 나머지 부분으로 주제를 판정한다.
_GENERIC_PROCEDURE_MORPHEMES: tuple[str, ...] = tuple(
    sorted(
        {
            "초음파",
            "내시경",
            "검사",
            "촬영",
            "수술",
            "시술",
            "치료",
            "요법",
            "진료",
            "진단",
            "질환",
            "관리",
            "예방",
            "증상",
            "원인",
            "안내",
            "정보",
            "방법",
        },
        key=len,
        reverse=True,
    )
)


def _distinctive_topic(topic: str) -> str:
    text = normalize_topic_text(_PARENTHETICAL.sub(" ", topic))
    for morpheme in _GENERIC_PROCEDURE_MORPHEMES:
        text = text.replace(morpheme, "")
    return text


def page_topic_related(topic: str, topic_terms: Sequence[str]) -> bool | None:
    """실제 문서 주제가 글 주제와 맞는가. 판단할 수 없으면 None.

    검사·시술 일반어를 걷어낸 나머지(부위·질환)로 채점한다. 나머지가 한 글자 이하면
    문서 주제 전체가 글 주제 안에 그대로 있어야 한다(예: '위내시경').
    """

    if not topic_terms:
        return None
    core = normalize_topic_text(_PARENTHETICAL.sub(" ", topic))
    if not core or not _HANGUL.search(core):
        return None
    distinct = _distinctive_topic(topic)
    if len(distinct) < 2:
        return core in normalize_topic_text(" ".join(topic_terms))
    score = reference_topic_match(
        distinct, list(topic_terms), ignored_tokens=institution_title_tokens()
    )
    if score is None:
        return None
    return score >= REFERENCE_TOKEN_MATCH_MIN


@dataclass(frozen=True, slots=True)
class PageJudgement:
    verdict: str
    reason: str
    page_title: str = ""
    text_len: int = 0
    topic: str = ""


def judge_fetched_page(
    url: str,
    fetched: FetchResult,
    topic_terms: Sequence[str],
    *,
    curated: bool,
) -> PageJudgement:
    """한 번의 GET 관측을 판정한다. 모델 라벨은 입력으로 받지도 않는다.

    `curated`는 주소나 GET의 최종 주소가 수기 목록 문서라는 뜻이다 — 목록 문서로 리다이렉트된
    주소는 그 목록 항목의 카탈로그로 대조한다.
    """

    catalog_url = url if _serves_curated_document(url) else (fetched.final_url or url)
    title = html_page_title(fetched.html)
    body_text = html_body_text(fetched.html) if fetched.html else ""
    text_len = len(body_text)

    def judgement(verdict: str, reason: str, topic: str = "") -> PageJudgement:
        return PageJudgement(verdict, reason, title, text_len, topic)

    if fetched.error is not None or fetched.status is None:
        # 접속 자체를 확인하지 못했다(시간 초과·연결 오류·오프라인·도메인 장애).
        if curated and curated_topic_relevant(catalog_url, topic_terms):
            return judgement(VERDICT_PASS, REASON_CURATED_UNREACHABLE)
        if curated:
            return judgement(VERDICT_FAIL, REASON_UNRELATED)
        reason = REASON_NOT_VERIFIED if fetched.error == FETCH_ERROR_OFFLINE else REASON_UNDETERMINABLE
        return judgement(VERDICT_FAIL, reason)

    status = int(fetched.status)
    if status in {404, 410}:
        return judgement(VERDICT_FAIL, REASON_DEAD_LINK)
    final_url = fetched.final_url or url
    if not is_whitelisted_url(final_url):
        return judgement(VERDICT_FAIL, REASON_REDIRECT_OUTSIDE)
    if status != 200:
        # 403/429(봇 차단)·5xx·203 등은 문서 내용을 볼 수 없다.
        if curated and curated_topic_relevant(catalog_url, topic_terms):
            return judgement(VERDICT_PASS, REASON_CURATED_UNREACHABLE)
        if curated:
            return judgement(VERDICT_FAIL, REASON_UNRELATED)
        if status >= 400 and status not in {401, 403, 429}:
            return judgement(VERDICT_FAIL, REASON_HTTP_ERROR)
        return judgement(VERDICT_FAIL, REASON_UNDETERMINABLE)
    if _is_soft_404(url, final_url):
        return judgement(VERDICT_FAIL, REASON_SOFT_404)
    host = _host(final_url)
    if _is_error_title(title):
        return judgement(VERDICT_FAIL, REASON_ERROR_PAGE)
    if _is_empty_template(title, body_text, host):
        return judgement(VERDICT_FAIL, REASON_EMPTY_TEMPLATE)
    if _is_stats_or_menu(url, final_url, title, curated=curated):
        return judgement(VERDICT_FAIL, REASON_STATS_OR_MENU)

    if curated:
        # 수기 목록의 주제는 사람이 확인한 카탈로그가 말한다.
        if curated_topic_relevant(catalog_url, topic_terms):
            return judgement(VERDICT_PASS, REASON_CURATED_VERIFIED)
        return judgement(VERDICT_FAIL, REASON_UNRELATED)

    topic = page_topic(title, host)
    if not topic or not _HANGUL.search(topic):
        # 영문 전용·기관명뿐인 제목은 주제를 판단할 수 없다 → 목록 밖이면 제거.
        return judgement(VERDICT_FAIL, REASON_UNDETERMINABLE, topic)
    related = page_topic_related(topic, topic_terms)
    if related is None:
        return judgement(VERDICT_FAIL, REASON_UNDETERMINABLE, topic)
    if not related:
        return judgement(VERDICT_FAIL, REASON_UNRELATED, topic)
    # 제목만 맞고 본문이 다른 문서(빈 본문·다른 질환 본문)를 거른다 — 문서 주제가 본문에도
    # 실제로 나와야 한다.
    normalized_body = normalize_topic_text(body_text)
    topic_tokens = [
        normalize_topic_text(token)
        for token in re.split(r"[^0-9A-Za-z가-힣]+", topic)
        if len(normalize_topic_text(token)) >= 2 and _HANGUL.search(token)
    ]
    if not any(token in normalized_body for token in topic_tokens):
        return judgement(VERDICT_FAIL, REASON_UNRELATED, topic)
    return judgement(VERDICT_PASS, REASON_PAGE_VERIFIED, topic)


# ── 검증 기록 ────────────────────────────────────────────────────────────────


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def reference_check_record(
    url: str,
    *,
    verdict: str,
    reason: str,
    checked_at: datetime,
    curated: bool,
    status: int | None = None,
    final_url: str | None = None,
    page_title: str = "",
    text_len: int = 0,
    fetch_error: str | None = None,
    verified_at: datetime | str | None = None,
    topic_fingerprint: str | None = None,
) -> dict[str, Any]:
    if isinstance(verified_at, datetime):
        verified_value: str | None = _iso(verified_at)
    else:
        verified_value = verified_at
    return {
        "url": url,
        "url_fingerprint": reference_url_fingerprint(url),
        "final_url": final_url,
        "status": status,
        "page_title": (page_title or "")[:REFERENCE_PAGE_TITLE_MAX_CHARS],
        "text_len": int(text_len or 0),
        "verdict": verdict,
        "reason": reason,
        "curated": bool(curated),
        "fetch_error": fetch_error,
        "checked_at": _iso(checked_at),
        # 마지막으로 **실제로** 통과를 확인한 시각. 장애 폴백 재사용 기간의 기준이다.
        "verified_at": verified_value,
        # 이 판정에 쓴 글 주제의 지문. 지금 글의 주제 지문과 다르면 신선한 통과가 아니다.
        "topic_fingerprint": topic_fingerprint,
        "schema_version": REFERENCE_CHECKS_SCHEMA_VERSION,
    }


def index_reference_checks(checks: object) -> dict[str, dict[str, Any]]:
    """지문 → 가장 최근 기록."""

    indexed: dict[str, dict[str, Any]] = {}
    if not isinstance(checks, list):
        return indexed
    for check in checks:
        if not isinstance(check, Mapping):
            continue
        fingerprint = check.get("url_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            continue
        current = indexed.get(fingerprint)
        if current is None or str(check.get("checked_at") or "") >= str(
            current.get("checked_at") or ""
        ):
            indexed[fingerprint] = dict(check)
    return indexed


def _age_within(value: object, *, now: datetime, limit: timedelta) -> bool:
    """`-시계 차이 허용 <= now - value <= limit`. 미래 시각은 허용 폭을 넘으면 거절한다."""

    moment = _parse_time(value)
    if moment is None:
        return False
    age = now - moment
    return -REFERENCE_CHECK_CLOCK_SKEW <= age <= limit


def _same_topic(check: Mapping[str, Any], topic_fingerprint: str) -> bool:
    stored = check.get("topic_fingerprint")
    return isinstance(stored, str) and bool(stored) and stored == topic_fingerprint


def _final_url_excluded(check: Mapping[str, Any]) -> bool:
    # 제외 문서로 리다이렉트되는 주소의 통과 기록(수정 전 코드가 남긴 것)은 통과가 아니다.
    return reference_exclusion_reason(check.get("final_url")) is not None


def check_is_fresh_pass(
    check: Mapping[str, Any] | None, *, now: datetime, topic_fingerprint: str
) -> bool:
    if not check or check.get("verdict") != VERDICT_PASS or _final_url_excluded(check):
        return False
    if not _same_topic(check, topic_fingerprint):
        return False
    return _age_within(check.get("checked_at"), now=now, limit=REFERENCE_CHECK_MAX_AGE)


def _reusable_previous_pass(
    check: Mapping[str, Any] | None, *, now: datetime, topic_fingerprint: str
) -> bool:
    if not check or check.get("verdict") != VERDICT_PASS or _final_url_excluded(check):
        return False
    if not _same_topic(check, topic_fingerprint):
        return False
    return _age_within(check.get("verified_at"), now=now, limit=REFERENCE_CHECK_REUSE_WINDOW)


@dataclass(frozen=True, slots=True)
class ReferenceGateStatus:
    """발행 직전 게이트의 판정 — 모든 참고자료에 같은 URL의 신선한 통과 기록이 있는가."""

    current: bool
    unverified_urls: tuple[str, ...] = ()
    malformed_entries: int = 0


def split_reference_entries(references: object) -> tuple[list[dict[str, Any]], int]:
    """(주소가 있는 매핑 항목, 형식이 깨진 항목 수). 목록이 아닌 값은 통째로 깨진 한 건이다."""

    if references is None:
        return [], 0
    if not isinstance(references, list):
        return [], 1
    entries: list[dict[str, Any]] = []
    malformed = 0
    for reference in references:
        if isinstance(reference, Mapping) and str(reference.get("url") or "").strip():
            entries.append(dict(reference))
        else:
            malformed += 1
    return entries, malformed


def reference_gate_status(
    references: object,
    checks: object,
    *,
    topic_terms: Sequence[str],
    now: datetime | None = None,
) -> ReferenceGateStatus:
    """모든 참고자료에 같은 URL·같은 글 주제의 신선한 통과 기록이 있는가. 깨진 항목은 실패다."""

    observed = now or datetime.now(timezone.utc)
    fingerprint = topic_fingerprint(topic_terms)
    indexed = index_reference_checks(checks)
    entries, malformed = split_reference_entries(references)
    missing: list[str] = []
    for reference in entries:
        url = str(reference.get("url") or "").strip()
        check = indexed.get(reference_url_fingerprint(url))
        if reference_exclusion_reason(url) is not None or not check_is_fresh_pass(
            check, now=observed, topic_fingerprint=fingerprint
        ):
            missing.append(url)
    return ReferenceGateStatus(
        current=not missing and not malformed,
        unverified_urls=tuple(missing),
        malformed_entries=malformed,
    )


def reason_label(reason: object) -> str:
    return REASON_LABELS.get(str(reason or ""), str(reason or ""))


# ── 검증기 ───────────────────────────────────────────────────────────────────


@dataclass
class ReferenceVerification:
    kept: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    deferred: list[dict[str, Any]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.deferred

    def failed_checks(self) -> list[dict[str, Any]]:
        return [check for check in self.checks if check.get("verdict") == VERDICT_FAIL]

    def deferred_checks(self) -> list[dict[str, Any]]:
        """일시 장애로 미룬 기록(실행당 GET 한도로 미룬 항목은 기록이 없다)."""

        return [check for check in self.checks if check.get("verdict") == VERDICT_DEFERRED]


class ReferenceVerifier:
    """GET 한도와 도메인 장애 상태를 실행 단위로 들고 다니는 검증기.

    한 실행(생성 1회, 발행기 1회, 07:45 게이트 1회, 관리자 요청 1회)이 인스턴스 하나를
    쓴다. 이벤트 루프에 묶이는 세마포어는 `verify` 호출마다 새로 만든다.
    """

    def __init__(
        self,
        fetcher: ReferenceFetcher | None = None,
        *,
        max_fetches: int = REFERENCE_FETCH_MAX_PER_RUN,
        timeout: float = REFERENCE_FETCH_TIMEOUT_SECONDS,
        max_concurrency: int = REFERENCE_FETCH_MAX_CONCURRENCY,
        per_domain_concurrency: int = REFERENCE_FETCH_PER_DOMAIN_CONCURRENCY,
        domain_spacing: float = REFERENCE_FETCH_DOMAIN_SPACING_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._fetcher = fetcher if fetcher is not None else default_reference_fetcher()
        self.max_fetches = max(0, int(max_fetches))
        self.timeout = float(timeout)
        self.max_concurrency = max(1, int(max_concurrency))
        self.per_domain_concurrency = max(1, int(per_domain_concurrency))
        self.domain_spacing = max(0.0, float(domain_spacing))
        self._clock = clock
        self._sleep = sleep
        self.fetch_count = 0
        self.down_domains: set[str] = set()
        self._cache: dict[str, FetchResult] = {}
        self._last_request_at: dict[str, float] = {}

    @property
    def budget_left(self) -> int:
        return max(0, self.max_fetches - self.fetch_count)

    async def fetch(
        self,
        url: str,
        *,
        semaphore: asyncio.Semaphore,
        domain_semaphores: dict[str, asyncio.Semaphore],
    ) -> FetchResult:
        cached = self._cache.get(url)
        if cached is not None:
            return cached
        host = _host(url)
        if host in self.down_domains:
            return FetchResult(url=url, error=FETCH_ERROR_DOMAIN_DOWN)
        if self.fetch_count >= self.max_fetches:
            return FetchResult(url=url, error=FETCH_ERROR_BUDGET)
        self.fetch_count += 1
        async with semaphore:
            async with domain_semaphores[host]:
                if host in self.down_domains:
                    # 기다리는 동안 같은 도메인이 내려갔다고 판정됐다 — GET하지 않았으니
                    # 한도에서도 되돌린다.
                    self.fetch_count -= 1
                    return FetchResult(url=url, error=FETCH_ERROR_DOMAIN_DOWN)
                last = self._last_request_at.get(host)
                if last is not None and self.domain_spacing:
                    wait = self.domain_spacing - (self._clock() - last)
                    if wait > 0:
                        await self._sleep(wait)
                try:
                    result = await asyncio.wait_for(self._fetcher(url), timeout=self.timeout)
                except Exception as exc:  # noqa: BLE001 — 주입된 fetcher의 예외도 여기서 멈춘다.
                    result = FetchResult(url=url, error=_classify_fetch_exception(exc))
                finally:
                    self._last_request_at[host] = self._clock()
        if not isinstance(result, FetchResult):
            result = FetchResult(url=url, error=FETCH_ERROR_OTHER)
        if result.error in _DOMAIN_DOWN_ERRORS or (
            result.status is not None and result.status >= 500
        ):
            self.down_domains.add(host)
        self._cache[url] = result
        return result

    async def verify(
        self,
        references: Sequence[Mapping[str, Any]],
        *,
        topic_terms: Sequence[str],
        previous_checks: object = None,
        reuse_fresh_checks: bool = False,
        defer_transient: bool = False,
        now: datetime | None = None,
    ) -> ReferenceVerification:
        """참고자료마다 판정한다.

        `defer_transient`(발행 직전·관리자 경로): 재사용할 직전 통과가 없는 목록 밖 URL의
        일시 장애(연결·시간 초과·프로토콜·5xx·408/429·도메인 장애)는 제거하지 않고
        `VERDICT_DEFERRED` 기록과 함께 `deferred`로 돌려준다. 생성 단계는 끈 채로 부른다 —
        판단 불가인 목록 밖 URL은 제거되고 수기 목록 치유가 이어진다.
        """

        observed = now or datetime.now(timezone.utc)
        fingerprint = topic_fingerprint(topic_terms)
        previous = index_reference_checks(previous_checks)
        semaphore = asyncio.Semaphore(self.max_concurrency)
        domain_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self.per_domain_concurrency)
        )

        async def verify_one(reference: Mapping[str, Any]) -> dict[str, Any] | None:
            url = str(reference.get("url") or "").strip()
            curated = _serves_curated_document(url)
            if reference_exclusion_reason(url) is not None:
                # 사람이 확인해 제외한 주소 — 저장된 통과를 재사용하지도, 다시 열지도 않는다.
                return reference_check_record(
                    url,
                    verdict=VERDICT_FAIL,
                    reason=REASON_EXCLUDED_SOURCE,
                    checked_at=observed,
                    curated=curated,
                    topic_fingerprint=fingerprint,
                )
            prior = previous.get(reference_url_fingerprint(url))
            if reuse_fresh_checks and check_is_fresh_pass(
                prior, now=observed, topic_fingerprint=fingerprint
            ):
                return dict(prior)
            if not url or not is_citable_reference_url(url):
                return reference_check_record(
                    url,
                    verdict=VERDICT_FAIL,
                    reason=REASON_NOT_CITABLE,
                    checked_at=observed,
                    curated=curated,
                    topic_fingerprint=fingerprint,
                )
            fetched = await self.fetch(
                url, semaphore=semaphore, domain_semaphores=domain_semaphores
            )
            if reference_exclusion_reason(fetched.final_url) is not None:
                # 제외 문서로 리다이렉트되는 주소는 그 제외 문서다 — 직접 인용한 것과 같다.
                return reference_check_record(
                    url,
                    verdict=VERDICT_FAIL,
                    reason=REASON_EXCLUDED_SOURCE,
                    checked_at=observed,
                    curated=curated,
                    status=fetched.status,
                    final_url=fetched.final_url,
                    topic_fingerprint=fingerprint,
                )
            if not curated and _serves_curated_document(fetched.final_url):
                # 목록 문서로 리다이렉트되는 주소는 그 목록 문서다(진료비·병원 선택 글이 뺀다).
                curated = True
            if fetched.error == FETCH_ERROR_BUDGET and not curated:
                # 목록 밖 URL은 열어 보지 않고는 판정할 수 없다 — 다음 실행으로 미룬다.
                # 수기 목록 URL은 아래에서 카탈로그 주제 대조로 판정한다(GET이 필요 없다).
                return None
            transient = is_transient_fetch(fetched)
            if transient and _reusable_previous_pass(
                prior, now=observed, topic_fingerprint=fingerprint
            ):
                # 기관 사이트 장애가 대량 보류가 되지 않게, 기간 안의 직전 통과를 쓴다.
                assert prior is not None
                return reference_check_record(
                    url,
                    verdict=VERDICT_PASS,
                    reason=REASON_REUSED_PREVIOUS_PASS,
                    checked_at=observed,
                    curated=curated,
                    status=fetched.status,
                    final_url=fetched.final_url,
                    page_title=str(prior.get("page_title") or ""),
                    text_len=int(prior.get("text_len") or 0),
                    fetch_error=fetched.error,
                    verified_at=str(prior.get("verified_at")),
                    topic_fingerprint=fingerprint,
                )
            if transient and defer_transient and not curated:
                # 기관 사이트 일시 장애는 문서가 없다는 증거가 아니다. 제거·치유·보류하지 않고
                # 다음 시간대 발행기가 다시 연다(수기 목록 URL은 카탈로그 대조로 판정된다).
                return reference_check_record(
                    url,
                    verdict=VERDICT_DEFERRED,
                    reason=REASON_SITE_UNREACHABLE,
                    checked_at=observed,
                    curated=curated,
                    status=fetched.status,
                    final_url=fetched.final_url,
                    fetch_error=fetched.error,
                    topic_fingerprint=fingerprint,
                )
            judged = judge_fetched_page(url, fetched, topic_terms, curated=curated)
            # 실제로 문서를 열어 확인한 통과만 장애 폴백 재사용의 기준 시각이 된다.
            actually_seen = judged.verdict == VERDICT_PASS and judged.reason in {
                REASON_PAGE_VERIFIED,
                REASON_CURATED_VERIFIED,
            }
            return reference_check_record(
                url,
                verdict=judged.verdict,
                reason=judged.reason,
                checked_at=observed,
                curated=curated,
                status=fetched.status,
                final_url=fetched.final_url,
                page_title=judged.page_title,
                text_len=judged.text_len,
                fetch_error=fetched.error,
                verified_at=observed if actually_seen else None,
                topic_fingerprint=fingerprint,
            )

        references = [ref for ref in references if isinstance(ref, Mapping)]
        records = await asyncio.gather(*(verify_one(reference) for reference in references))
        outcome = ReferenceVerification()
        for reference, record in zip(references, records, strict=True):
            if record is None:
                outcome.deferred.append(dict(reference))
                continue
            outcome.checks.append(record)
            if record.get("verdict") == VERDICT_DEFERRED:
                outcome.deferred.append(dict(reference))
                continue
            if record.get("verdict") == VERDICT_PASS:
                outcome.kept.append(dict(reference))
            else:
                outcome.dropped.append(dict(reference))
                logger.info(
                    "Dropping reference host=%s reason=%s status=%s",
                    _host(record.get("url")),
                    record.get("reason"),
                    record.get("status"),
                )
        return outcome


def names_curated_document(reference: Mapping[str, Any], checks: object = None) -> bool:
    """이 참고자료가 수기 목록 문서인가 — 주소가 목록 문서(별칭 포함)이거나, 그 주소의 최근
    검증 기록에서 GET의 최종 주소가 목록 문서다(목록 문서로 리다이렉트되는 주소)."""

    url = str(reference.get("url") or "").strip()
    if is_curated_source_url(url):
        return True
    check = index_reference_checks(checks).get(reference_url_fingerprint(url))
    return check is not None and is_curated_source_url(check.get("final_url"))


def merge_reference_checks(*groups: object) -> list[dict[str, Any]]:
    """여러 번의 검증 기록을 지문별 최신 하나로 합친다."""

    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for fingerprint, check in index_reference_checks(group).items():
            current = merged.get(fingerprint)
            if current is None or str(check.get("checked_at") or "") >= str(
                current.get("checked_at") or ""
            ):
                merged[fingerprint] = check
    return sorted(merged.values(), key=lambda check: str(check.get("url") or ""))


def drop_notes(failed_checks: Iterable[Mapping[str, Any]], *, host_chars: int = 32) -> list[str]:
    """'호스트(사유)' 목록 — 재작성 지적과 로그에 쓴다."""

    notes: list[str] = []
    for check in failed_checks:
        host = _host(check.get("url")) or str(check.get("url") or "")
        note = f"{host[:host_chars]}({reason_label(check.get('reason'))})"
        if note not in notes:
            notes.append(note)
    return notes
